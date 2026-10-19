"""Face repository — SQLite metadata + Milvus vector operations."""
import time
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.face import Face
from core.milvus import get_client
from core.config import settings
import numpy as np


# ---- SQLite operations ----

def create_face_record(db: Session, student_id: int, image_path: str) -> Face:
    """Create face metadata record. face_id assigned after Milvus insert."""
    face = Face(face_id=0, student_id=student_id, image_path=image_path)
    db.add(face)
    db.commit()
    db.refresh(face)
    return face


def get_faces_by_student(db: Session, student_id: int) -> list[Face]:
    return db.execute(
        select(Face).where(Face.student_id == student_id).order_by(Face.created_at.desc())
    ).scalars().all()


def get_face_by_id(db: Session, face_id: int) -> Face | None:
    return db.execute(
        select(Face).where(Face.face_id == face_id)
    ).scalar_one_or_none()


def get_all_face_ids_by_student(db: Session, student_id: int) -> list[int]:
    rows = db.execute(
        select(Face.face_id).where(Face.student_id == student_id)
    ).scalars().all()
    return list(rows)


def delete_face_record(db: Session, face_id: int) -> bool:
    face = db.execute(select(Face).where(Face.face_id == face_id)).scalar_one_or_none()
    if not face:
        return False
    db.delete(face)
    db.commit()
    return True


def count_faces_by_student(db: Session, student_id: int) -> int:
    return db.execute(
        select(func.count(Face.id)).where(Face.student_id == student_id)
    ).scalar()


def get_all_faces(db: Session) -> list[Face]:
    return db.execute(select(Face)).scalars().all()


# ---- Milvus operations ----

def insert_vector(face_id: int, student_id: int, embedding: np.ndarray):
    c = get_client()
    c.insert(
        collection_name=settings.COLLECTION_NAME,
        data=[{
            "face_id": face_id,
            "student_id": student_id,
            "embedding": embedding.tolist(),
            "created_at": int(time.time()),
        }],
    )


def delete_vectors(face_ids: list[int]):
    c = get_client()
    if face_ids:
        c.delete(
            collection_name=settings.COLLECTION_NAME,
            filter=f"face_id in {face_ids}",
        )


def search_similar(embedding: np.ndarray, top_k: int = 5) -> list[dict]:
    c = get_client()
    results = c.search(
        collection_name=settings.COLLECTION_NAME,
        data=[embedding.tolist()],
        limit=top_k,
        output_fields=["face_id", "student_id", "created_at"],
        search_params={"metric_type": "COSINE"},
    )
    hits = []
    if results and results[0]:
        for hit in results[0]:
            hits.append({
                "face_id": hit.entity.get("face_id"),
                "student_id": hit.entity.get("student_id"),
                "similarity": float(hit.distance),
            })
    return hits


def get_all_vectors() -> list[dict]:
    """Pull all vectors for offline clustering (ML audit)."""
    c = get_client()
    results = c.query(
        collection_name=settings.COLLECTION_NAME,
        filter="",
        output_fields=["face_id", "student_id", "embedding", "created_at"],
        limit=16384,
    )
    return results if results else []
