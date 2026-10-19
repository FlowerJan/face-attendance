"""Face DTOs."""
from datetime import datetime
from pydantic import BaseModel


class FaceOut(BaseModel):
    id: int
    face_id: int
    student_id: int
    image_path: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class FaceEnrollResult(BaseModel):
    detected: bool
    face_id: int | None = None
    image_path: str | None = None
    reason: str | None = None
