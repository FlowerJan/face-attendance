"""Student service — business logic, cascade delete orchestration."""
import os
from sqlalchemy.orm import Session
from schemas.student import StudentCreate, StudentUpdate
from schemas.common import PageResult
from repository import student_repo, face_repo
from core.config import settings


def create_student(db: Session, data: StudentCreate) -> dict:
    existing = student_repo.get_by_student_no(db, data.student_no)
    if existing:
        return {"code": 1, "message": f"student_no {data.student_no} already exists"}
    student = student_repo.create(db, data)
    return {"code": 0, "data": _serialize(student)}


def get_student(db: Session, student_id: int) -> dict:
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}
    return {"code": 0, "data": _serialize(student)}


def list_students(db: Session, page: int, page_size: int, keyword: str | None, class_name: str | None) -> dict:
    items, total = student_repo.list_paginated(db, page, page_size, keyword, class_name)
    return {"code": 0, "data": {
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(s) for s in items],
    }}


def update_student(db: Session, student_id: int, data: StudentUpdate) -> dict:
    student = student_repo.update(db, student_id, data)
    if not student:
        return {"code": 1, "message": "student not found"}
    return {"code": 0, "data": _serialize(student)}


def delete_student(db: Session, student_id: int) -> dict:
    """
    Cascade delete: SQLite (CASCADE) + Milvus (manual) + disk files (manual).
    Orchestrated here in Service layer.
    """
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}

    # 必须在删除前取出标量：commit 后 ORM 实例会过期，再访问属性会抛 ObjectDeletedError
    student_no = student.student_no

    # 1. get all face_ids before SQLite cascade-delete wipes them
    face_ids = face_repo.get_all_face_ids_by_student(db, student_id)

    # 2. get all image paths before cascade
    faces = face_repo.get_faces_by_student(db, student_id)
    image_paths = [f.image_path for f in faces]

    # 3. delete SQLite record (faces table auto-cascade via FK ON DELETE CASCADE)
    #    注意：SQLite 需 PRAGMA foreign_keys=ON 才真正级联，见 core/db.py
    student_repo.delete(db, student_id)

    # 4. delete Milvus vectors
    deleted_vectors = 0
    if face_ids:
        face_repo.delete_vectors(face_ids)
        face_repo.remove_active(face_ids)  # M5：同步撤出有效人脸索引
        deleted_vectors = len(face_ids)

    # 5. delete disk image files
    deleted_files: list[str] = []
    for path in image_paths:
        full_path = os.path.join(str(settings.BASE_DIR), path.lstrip("/"))
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
                deleted_files.append(path)
        except Exception:  # noqa: BLE001
            pass  # log but don't fail the whole operation

    # 6. 清理空目录
    if image_paths:
        import shutil
        student_dir = os.path.join(str(settings.FACE_IMG_DIR), student_no)
        if os.path.isdir(student_dir) and not os.listdir(student_dir):
            shutil.rmtree(student_dir, ignore_errors=True)

    return {"code": 0, "message": "student and all associated faces deleted", "data": {
        "deleted_faces": len(face_ids),
        "deleted_vectors": deleted_vectors,
        "deleted_files": deleted_files,
    }}


def import_students_csv(db: Session, file_path: str) -> dict:
    """Batch import students from CSV/Excel."""
    import pandas as pd
    df = pd.read_csv(file_path)
    required = ["student_no", "name"]
    for col in required:
        if col not in df.columns:
            return {"code": 1, "message": f"missing column: {col}"}

    imported, skipped = 0, 0
    for _, row in df.iterrows():
        existing = student_repo.get_by_student_no(db, str(row["student_no"]))
        if existing:
            skipped += 1
            continue
        data = StudentCreate(
            student_no=str(row["student_no"]),
            name=str(row["name"]),
            gender=str(row.get("gender", "")) or None,
            class_name=str(row.get("class_name", "")) or None,
            major=str(row.get("major", "")) or None,
            enroll_year=int(row["enroll_year"]) if "enroll_year" in row and pd.notna(row.get("enroll_year")) else None,
            phone=str(row.get("phone", "")) or None,
        )
        student_repo.create(db, data)
        imported += 1

    return {"code": 0, "data": {"imported": imported, "skipped": skipped}}


def _serialize(student) -> dict:
    return {
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "gender": student.gender,
        "class_name": student.class_name,
        "major": student.major,
        "enroll_year": student.enroll_year,
        "phone": student.phone,
        "face_status": student.face_status,
        "created_at": str(student.created_at) if student.created_at else None,
        "updated_at": str(student.updated_at) if student.updated_at else None,
    }
