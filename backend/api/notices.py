"""Notice controller — REST CRUD + WebSocket real-time push."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
from api.deps import get_db_session
from schemas.notice import NoticeCreate, NoticeUpdate
from service import notice_service
from core.ws_manager import ws_manager

router = APIRouter(prefix="/api/notices", tags=["notices"])


@router.get("", summary="公告列表（分页）")
def list_notices(page: int = 1, page_size: int = 20, db: Session = Depends(get_db_session)):
    return notice_service.list_notices(db, page, page_size)


@router.get("/{notice_id}", summary="公告详情")
def get_notice(notice_id: int, db: Session = Depends(get_db_session)):
    return notice_service.get_notice(db, notice_id)


@router.post("", summary="发布公告（发布后 WebSocket 实时推送前端）")
def create_notice(data: NoticeCreate, db: Session = Depends(get_db_session)):
    # 发布后由 service 层触发 WebSocket 广播
    return notice_service.create_notice(db, data)


@router.put("/{notice_id}", summary="更新公告")
def update_notice(notice_id: int, data: NoticeUpdate, db: Session = Depends(get_db_session)):
    return notice_service.update_notice(db, notice_id, data)


@router.delete("/{notice_id}", summary="删除公告")
def delete_notice(notice_id: int, db: Session = Depends(get_db_session)):
    return notice_service.delete_notice(db, notice_id)


# ---- WebSocket endpoint ----
ws_router = APIRouter()


@ws_router.websocket("/ws/notices")
async def websocket_notices(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_json()
            # 心跳：前端 ping → 服务端 pong
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        ws_manager.disconnect(ws)
