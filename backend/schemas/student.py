"""Student DTOs (corresponds to DTO/VO)."""
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from core.enums import EnumContainer


class StudentCreate(BaseModel):
    student_no: str = Field(..., max_length=20)
    name: str = Field(..., max_length=50)
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None

    @field_validator("class_name")
    @classmethod
    def validate_class(cls, v):
        if v and not EnumContainer.is_valid_class(v):
            raise ValueError(f"invalid class: {v}")
        return v

    @field_validator("major")
    @classmethod
    def validate_major(cls, v):
        if v and not EnumContainer.is_valid_major(v):
            raise ValueError(f"invalid major: {v}")
        return v


class StudentUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None


class StudentOut(BaseModel):
    id: int
    student_no: str
    name: str
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None
    face_status: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
