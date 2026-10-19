"""Notice repository."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.notice import Notice
from schemas.notice import NoticeCreate, NoticeUpdate


def create(db: Session, data: NoticeCreate) -> Notice:
    notice = Notice(**data.model_dump())
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return notice


def get_by_id(db: Session, notice_id: int) -> Notice | None:
    return db.get(Notice, notice_id)


def list_paginated(db: Session, page: int = 1, page_size: int = 20) -> tuple[list[Notice], int]:
    total = db.execute(select(func.count(Notice.id))).scalar()
    items = db.execute(
        select(Notice).order_by(Notice.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total


def update(db: Session, notice_id: int, data: NoticeUpdate) -> Notice | None:
    notice = db.get(Notice, notice_id)
    if not notice:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(notice, k, v)
    db.commit()
    db.refresh(notice)
    return notice


def delete(db: Session, notice_id: int) -> bool:
    notice = db.get(Notice, notice_id)
    if not notice:
        return False
    db.delete(notice)
    db.commit()
    return True
