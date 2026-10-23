"""System controller — health check + runtime status (corresponds to @RestController)."""
from __future__ import annotations

import os

from fastapi import APIRouter

from core import face_engine
from core.config import settings
from core.milvus import get_client
from core.ws_manager import ws_manager

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", summary="健康检查")
def health():
    """存活探针，供联调脚本 / 前端健康检查使用。"""
    return {"code": 0, "message": "ok", "data": {"status": "up"}}


@router.get("/system/status", summary="运行时状态（向量库 / 模型 / WebSocket 在线数）")
def system_status():
    """运行时状态：向量库后端与规模、人脸模型加载情况、WS 在线数。"""
    backend = "unavailable"
    vector_count = 0
    vector_error = None
    try:
        client = get_client()
        backend = "milvus-lite"
        stats = client.get_collection_stats(settings.COLLECTION_NAME)
        vector_count = int(stats.get("row_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        vector_error = f"{type(exc).__name__}: {exc}"

    model_dir = os.path.join(
        os.path.expanduser("~"), ".insightface", "models", settings.FACE_MODEL_NAME
    )
    data = {
        "app": "学生刷脸考勤系统 · 第一阶段",
        "vector_backend": backend,
        "vector_count": vector_count,
        "face_engine": face_engine.status(),
        "face_model_dir": model_dir,
        "face_model_installed": os.path.isdir(model_dir),
        "ws_online": len(ws_manager.active),
    }
    if vector_error:
        data["vector_error"] = vector_error
    return {"code": 0, "message": "ok", "data": data}
