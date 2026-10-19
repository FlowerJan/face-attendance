"""Notice DTOs."""
from datetime import datetime
from pydantic import BaseModel


class NoticeCreate(BaseModel):
    title: str
    content: str | None = None
    publisher: str | None = None


class NoticeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    publisher: str | None = None


class NoticeOut(BaseModel):
    id: int
    title: str
    content: str | None = None
    publisher: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
