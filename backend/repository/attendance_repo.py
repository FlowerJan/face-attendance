"""Attendance data access layer."""
from datetime import datetime

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from models.attendance import Attendance
from models.student import Student


def create_record(db: Session, student_id: int, face_id: int, similarity: float,
                  checkin_date: str, *, top2_similarity: float | None = None,
                  margin: float | None = None, ambiguous: int = 0) -> Attendance:
    record = Attendance(
        student_id=student_id,
        face_id=face_id,
        similarity=similarity,
        top2_similarity=top2_similarity,
        margin=margin,
        ambiguous=ambiguous,
        checkin_date=checkin_date,
        checkin_at=datetime.now(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_by_student_and_date(db: Session, student_id: int, checkin_date: str) -> Attendance | None:
    return (
        db.query(Attendance)
        .filter(Attendance.student_id == student_id, Attendance.checkin_date == checkin_date)
        .first()
    )


def list_by_date(db: Session, checkin_date: str, page: int = 1, page_size: int = 20) -> dict:
    query = (
        db.query(Attendance, Student)
        .join(Student, Attendance.student_id == Student.id)
        .filter(Attendance.checkin_date == checkin_date)
        .order_by(Attendance.checkin_at.desc())
    )
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "id": att.id,
            "student_id": s.id,
            "student_no": s.student_no,
            "name": s.name,
            "class_name": s.class_name,
            "face_id": att.face_id,
            "similarity": round(float(att.similarity), 4) if att.similarity is not None else None,
            "top2_similarity": round(float(att.top2_similarity), 4) if att.top2_similarity is not None else None,
            "margin": round(float(att.margin), 4) if att.margin is not None else None,
            "ambiguous": bool(att.ambiguous),
            "checkin_date": att.checkin_date,
            "checkin_at": str(att.checkin_at) if att.checkin_at else None,
        }
        for att, s in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def day_summary(db: Session, checkin_date: str) -> dict:
    """当日签到汇总：已签人数 / 在册学生总数 / 签到率。"""
    checked = (
        db.query(Attendance)
        .filter(Attendance.checkin_date == checkin_date)
        .count()
    )
    total_students = db.query(sa_func.count(Student.id)).scalar() or 0
    return {
        "date": checkin_date,
        "checked_count": checked,
        "total_students": total_students,
        "rate": round(checked / total_students, 4) if total_students else 0.0,
    }
