"""Face service — camera capture -> detect -> quality gate -> embed -> store (3 places)."""
import logging
import os

import cv2
import numpy as np
from sqlalchemy.orm import Session
from core.config import settings
from core.face_engine import detect_faces
from core.quality_gate import QualityGate
from repository import student_repo, face_repo
from service.cluster_service import CROSS_STUDENT_THRESHOLD

logger = logging.getLogger(__name__)

# 门控规则集是纯配置化的无状态对象，全局复用一份即可
_quality_gate = QualityGate()


def enroll_face(db: Session, student_id: int, image_bytes: bytes) -> dict:
    """
    Full pipeline: decode -> detect -> quality gate -> embed -> store.
    Stores: disk image + Milvus vector + SQLite metadata.

    质量门控放在 detect 之后、embed/存盘之前 ——
    脏图不允许进入库，否则后续识别失败时无法回溯原因。
    """
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}

    # 1. decode bytes to BGR numpy array
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"code": 1, "message": "invalid image data"}

    # 2. detect face + extract embedding（只跑一次检测，后面门控复用同一批结果）
    faces = detect_faces(img)
    if not faces:
        return {"code": 0, "data": {"detected": False, "reason": "no face detected"}}
    main_face = faces[0]

    # 3. 质量门控：不通过则拒绝入库，把可执行的提示直接回给前端
    quality = _quality_gate.check(img, main_face, faces)
    if not quality.passed:
        return {
            "code": 0,
            "data": {
                "detected": True,
                "enrolled": False,
                "reason": quality.reason,
                "rule": quality.rule,
                "quality": quality.to_dict(),
            },
        }

    embedding = main_face.embedding

    # 4. save image to disk (by student_no subdirectory)
    student_dir = os.path.join(str(settings.FACE_IMG_DIR), student.student_no)
    os.makedirs(student_dir, exist_ok=True)
    import time
    filename = f"{int(time.time() * 1000)}.jpg"
    img_path = os.path.join(student_dir, filename)
    cv2.imwrite(img_path, img)

    # relative path for storage
    rel_path = f"/uploads/faces/{student.student_no}/{filename}"

    # 5. create SQLite metadata record first (to get auto-increment id as face_id)
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

    # 6. insert vector into Milvus
    face_repo.insert_vector(face_repo_face_id, student_id, embedding)

    # 7. update student face_status
    student_repo.update_face_status(db, student_id, 1)

    # 8. 实时查重：与库中已有向量比对（Milvus 向量检索）
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
        "enrolled": True,
        "face_id": face_repo_face_id,
        "image_path": rel_path,
        "quality": quality.to_dict(),
        "similar_existing": similar_existing,
        "duplicate_suspect": bool(similar_existing),
    }}


def preview_quality(image_bytes: bytes) -> dict:
    """只做质量诊断，不落库、不写向量。

    用途：前端在真正提交录入之前先探一次，
    让用户看到「哪一项不达标、差多少」，而不是提交后才知道被拒。
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"code": 1, "message": "invalid image data"}

    faces = detect_faces(img)
    if not faces:
        return {"code": 0, "data": {"detected": False, "reason": "no face detected"}}

    quality = _quality_gate.check(img, faces[0], faces)
    return {
        "code": 0,
        "data": {
            "detected": True,
            "face_count": len(faces),
            "passed": quality.passed,
            "reason": quality.reason,
            "rule": quality.rule,
            "quality": quality.to_dict(),
        },
    }


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
