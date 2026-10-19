"""Face service — camera capture -> detect -> embed -> store (3 places)."""
import logging
import os

import cv2
import numpy as np
from sqlalchemy.orm import Session
from core.config import settings
from core.face_engine import detect_and_embed
from repository import student_repo, face_repo
from service.cluster_service import CROSS_STUDENT_THRESHOLD

logger = logging.getLogger(__name__)


def enroll_face(db: Session, student_id: int, image_bytes: bytes) -> dict:
    """
    Full pipeline: decode -> detect -> embed -> store.
    Stores: disk image + Milvus vector + SQLite metadata.
    """
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}

    # 1. decode bytes to BGR numpy array
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"code": 1, "message": "invalid image data"}

    # 2. detect face + extract embedding
    result = detect_and_embed(img)
    if result is None:
        return {"code": 0, "data": {"detected": False, "reason": "no face detected"}}

    embedding = result["embedding"]

    # 3. save image to disk (by student_no subdirectory)
    student_dir = os.path.join(str(settings.FACE_IMG_DIR), student.student_no)
    os.makedirs(student_dir, exist_ok=True)
    import time
    filename = f"{int(time.time() * 1000)}.jpg"
    img_path = os.path.join(student_dir, filename)
    cv2.imwrite(img_path, img)

    # relative path for storage
    rel_path = f"/uploads/faces/{student.student_no}/{filename}"

    # 4. create SQLite metadata record first (to get auto-increment id as face_id)
    face = face_repo.create_face_record(db, student_id, rel_path)
    # use the SQLite auto-increment id as Milvus face_id (ensures 1:1 mapping)
    face_repo_face_id = face.id

    # update face_id in the record
    face.face_id = face_repo_face_id
    from models.face import Face
    db_face = db.get(Face, face.id)
    db_face.face_id = face_repo_face_id
    db.commit()
    db.refresh(db_face)

    # 5. insert vector into Milvus
    face_repo.insert_vector(face_repo_face_id, student_id, embedding)

    # 6. update student face_status
    student_repo.update_face_status(db, student_id, 1)

    # 7. 实时查重：与库中已有向量比对（Milvus 向量检索）
    similar_existing = []
    try:
        for hit in face_repo.search_similar(embedding, top_k=5):
            if hit["face_id"] == face_repo_face_id:
                continue
            if hit["similarity"] < CROSS_STUDENT_THRESHOLD:
                continue
            other = student_repo.get_by_id(db, hit["student_id"])
            similar_existing.append({
                "face_id": hit["face_id"],
                "student_id": hit["student_id"],
                "student_no": other.student_no if other else "?",
                "name": other.name if other else "?",
                "similarity": round(float(hit["similarity"]), 4),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[face] 实时查重失败（不影响录入）: %s", exc)

    return {"code": 0, "data": {
        "detected": True,
        "face_id": face_repo_face_id,
        "image_path": rel_path,
        "similar_existing": similar_existing,
        "duplicate_suspect": bool(similar_existing),
    }}


def get_student_faces(db: Session, student_id: int) -> dict:
    faces = face_repo.get_faces_by_student(db, student_id)
    return {"code": 0, "data": [_serialize_face(f) for f in faces]}


def delete_face(db: Session, face_id: int) -> dict:
    """Delete face: SQLite + Milvus + disk."""
    face = face_repo.get_face_by_id(db, face_id)
    if not face:
        return {"code": 1, "message": "face not found"}

    student_id = face.student_id
    image_path = face.image_path

    # 1. delete SQLite record
    face_repo.delete_face_record(db, face_id)

    # 2. delete Milvus vector
    face_repo.delete_vectors([face_id])

    # 3. delete disk file
    full_path = os.path.join(str(settings.BASE_DIR), image_path.lstrip("/"))
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass

    # 4. update student face_status if no faces left
    remaining = face_repo.count_faces_by_student(db, student_id)
    if remaining == 0:
        student_repo.update_face_status(db, student_id, 0)

    return {"code": 0, "message": "face deleted"}


def _serialize_face(face) -> dict:
    return {
        "id": face.id,
        "face_id": face.face_id,
        "student_id": face.student_id,
        "image_path": face.image_path,
        "created_at": str(face.created_at) if face.created_at else None,
    }
