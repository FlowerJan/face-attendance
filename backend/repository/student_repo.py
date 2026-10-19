"""Student repository — pure data access, no business logic."""
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from models.student import Student
from schemas.student import StudentCreate, StudentUpdate


def create(db: Session, data: StudentCreate) -> Student:
    student = Student(**data.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def get_by_id(db: Session, student_id: int) -> Student | None:
    return db.get(Student, student_id)


def get_by_student_no(db: Session, student_no: str) -> Student | None:
    return db.execute(
        select(Student).where(Student.student_no == student_no)
    ).scalar_one_or_none()


def list_paginated(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    class_name: str | None = None,
) -> tuple[list[Student], int]:
    q = select(Student)
    count_q = select(func.count(Student.id))

    if keyword:
        pattern = f"%{keyword}%"
        cond = or_(
            Student.name.like(pattern),
            Student.student_no.like(pattern),
        )
        q = q.where(cond)
        count_q = count_q.where(cond)

    if class_name:
        q = q.where(Student.class_name == class_name)
        count_q = count_q.where(Student.class_name == class_name)

    total = db.execute(count_q).scalar()
    items = db.execute(
        q.order_by(Student.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return list(items), total


def update(db: Session, student_id: int, data: StudentUpdate) -> Student | None:
    student = db.get(Student, student_id)
    if not student:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(student, k, v)
    db.commit()
    db.refresh(student)
    return student


def delete(db: Session, student_id: int) -> bool:
    student = db.get(Student, student_id)
    if not student:
        return False
    db.delete(student)
    db.commit()
    return True


def update_face_status(db: Session, student_id: int, status: int):
    student = db.get(Student, student_id)
    if student:
        student.face_status = status
        db.commit()


def count_all(db: Session) -> int:
    return db.execute(select(func.count(Student.id))).scalar()
