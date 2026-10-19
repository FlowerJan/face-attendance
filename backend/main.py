"""Application entry point — assemble routes + middleware + startup events."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import settings
from core.db import init_db
from core.enums import EnumContainer
from core.milvus import get_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    print("[startup] loading enum data from CSV...")
    EnumContainer.load()
    print(f"  classes: {EnumContainer.get_classes()}")
    print(f"  majors: {EnumContainer.get_majors()}")

    print("[startup] initializing SQLite tables...")
    init_db()

    print("[startup] connecting Milvus Lite...")
    get_client()
    print(f"  collection: {settings.COLLECTION_NAME}")

    # WebSocket 广播需要主事件循环引用（service 层是同步代码）
    from core.ws_manager import ws_manager
    ws_manager.bind_loop(asyncio.get_running_loop())

    print("[startup] loading insightface model (buffalo_l)...")
    from core.face_engine import get_face_app
    try:
        get_face_app()
        print("[startup] face model ready.")
    except Exception as exc:  # noqa: BLE001
        # 模型缺失/下载失败时不阻塞服务启动，业务接口会返回明确错误
        print(f"[startup][WARN] face model not available: {exc}")
        print("[startup][WARN] 人脸录入接口将返回错误，其余功能可用。"
              "请手动下载 buffalo_l 到 ~/.insightface/models/buffalo_l/ 后重启。")
    print("[startup] all ready.")

    yield

    # ---- shutdown ----
    print("[shutdown] cleaning up...")


app = FastAPI(title="Face Attendance System", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# static files for face images
# 注意挂载点必须与库里存的 image_path 前缀一致：
#   image_path = /uploads/faces/<学号>/<文件名>
#   磁盘目录   = backend/uploads/faces/<学号>/<文件名>
# 若挂载成 "/uploads" 会解析到 uploads/faces/faces/... 导致图片全部 404
app.mount("/uploads/faces", StaticFiles(directory=str(settings.FACE_IMG_DIR)), name="uploads")


# ---- 统一异常响应：始终返回 {"code": non-zero, "message": ..., "data": null} ----
# 否则 FastAPI 默认返回 {"detail": ...}，前端拦截器拿不到可读 message
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors() or []
    first = errors[0] if errors else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path")) or "参数"
    msg = f"{loc}: {first.get('msg', '参数校验失败')}"
    # errors() 的 ctx 里可能含 ValueError 等不可 JSON 序列化的对象，需清洗
    clean = []
    for e in errors:
        item = {k: v for k, v in e.items() if k != "ctx"}
        if "input" in item:
            item["input"] = repr(item["input"])[:200]
        clean.append(item)
    return JSONResponse(status_code=422, content={"code": 422, "message": msg, "data": {"errors": clean}})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"{type(exc).__name__}: {exc}", "data": None},
    )


# register routers
from api.students import router as students_router, enum_router
from api.faces import router as faces_router
from api.stats import router as stats_router
from api.audit import router as audit_router
from api.notices import router as notices_router, ws_router
from api.system import router as system_router

app.include_router(system_router)
app.include_router(students_router)
app.include_router(enum_router)
app.include_router(faces_router)
app.include_router(stats_router)
app.include_router(audit_router)
app.include_router(notices_router)
app.include_router(ws_router)


@app.get("/")
def root():
    return {"code": 0, "message": "Face Attendance System API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
