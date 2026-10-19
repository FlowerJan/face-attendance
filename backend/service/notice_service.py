"""Notice service — CRUD + WebSocket broadcast orchestration."""
from sqlalchemy.orm import Session
from schemas.notice import NoticeCreate, NoticeUpdate
from repository import notice_repo
from core.ws_manager import ws_manager


def create_notice(db: Session, data: NoticeCreate) -> dict:
    notice = notice_repo.create(db, data)
    result = _serialize(notice)

    # broadcast via WebSocket to all connected clients
    ws_manager.broadcast({"type": "notice", "data": result})

    return {"code": 0, "data": result}


def get_notice(db: Session, notice_id: int) -> dict:
    notice = notice_repo.get_by_id(db, notice_id)
    if not notice:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "data": _serialize(notice)}


def list_notices(db: Session, page: int, page_size: int) -> dict:
    items, total = notice_repo.list_paginated(db, page, page_size)
    return {"code": 0, "data": {
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(n) for n in items],
    }}


def update_notice(db: Session, notice_id: int, data: NoticeUpdate) -> dict:
    notice = notice_repo.update(db, notice_id, data)
    if not notice:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "data": _serialize(notice)}


def delete_notice(db: Session, notice_id: int) -> dict:
    ok = notice_repo.delete(db, notice_id)
    if not ok:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "message": "notice deleted"}


def _serialize(notice) -> dict:
    return {
        "id": notice.id,
        "title": notice.title,
        "content": notice.content,
        "publisher": notice.publisher,
        "created_at": str(notice.created_at) if notice.created_at else None,
    }
