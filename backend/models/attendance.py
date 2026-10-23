"""Attendance record ORM entity — face check-in, one record per student per day."""
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base


class Attendance(Base):
    __tablename__ = "attendance"
    # 同一学生同一天只允许一条记录：重复签到在业务层拒绝，约束兜底防并发
    __table_args__ = (UniqueConstraint("student_id", "checkin_date", name="uq_student_day"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 命中的 Milvus face_id（1:N 识别的证据链），未识别时不产生记录所以可空仅备用
    face_id: Mapped[int | None] = mapped_column(Integer)
    similarity: Mapped[float | None] = mapped_column(Float)
    checkin_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    checkin_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
