"""Face service — camera capture -> detect -> quality gate -> embed -> store (3 places)."""
import logging
import os

import cv2
import numpy as np
from sqlalchemy.orm import Session
from core.config import settings
from core.face_engine import detect_faces
from core.quality_gate import QualityGate
from models.face import Face
from repository import student_repo, face_repo
from service.cluster_service import CROSS_STUDENT_THRESHOLD

logger = logging.getLogger(__name__)

# 门控规则集是纯配置化的无状态对象，全局复用一份即可
_quality_gate = QualityGate()


def _remove_disk_file(path: str) -> None:
    """回滚步骤：删除已落盘的图片。删不掉只记日志——孤儿文件的代价小于回滚中断。"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as exc:  # noqa: BLE001
        logger.error("[face] 回滚失败，磁盘文件需人工清理 %s: %s", path, exc)


def _delete_face_row(db: Session, row_id: int) -> None:
    """回滚步骤：删除 SQLite 记录。按 id 重新取对象，避免用已失效的 ORM 实例。"""
    try:
        obj = db.get(Face, row_id)
        if obj is not None:
            db.delete(obj)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("[face] 回滚失败，数据库记录需人工清理 id=%s: %s", row_id, exc)


def _rollback_enroll(db: Session, undo: list, face_id: int | None) -> None:
    """补偿式回滚：逆序撤销已完成的写入步骤。

    三处存储（文件系统 / SQLite / Milvus）之间没有分布式事务可用，
    只能靠"成功一步压一步、出错逆序撤销"来收敛。每一步都单独吞异常，
    保证前一步回滚失败不影响后一步——宁可留一条待人工清理的日志，
    也不能因为回滚链断掉而把已完成的写入留在半截状态。
    """
    try:
        # 先把可能处在坏事务里的 session 复位，否则后面的 delete 也 commit 不出去
        db.rollback()
    except Exception:  # noqa: BLE001
        pass

    for name, undo_fn in reversed(undo):
        try:
            undo_fn()
        except Exception as exc:  # noqa: BLE001
            logger.error("[face] 回滚步骤 %s 异常: %s", name, exc)

    # 兜底：insert_vector 抛异常时无法判定向量是否真的没写进去
    # （超时类失败可能服务端已成功），按 face_id 幂等删一次，删不掉也只是记日志
    if face_id is not None:
        try:
            face_repo.delete_vectors([face_id])
        except Exception as exc:  # noqa: BLE001
            logger.error("[face] 向量兜底删除失败 face_id=%s: %s", face_id, exc)


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

    # 4~6. 三写段：磁盘 / SQLite / Milvus
    #   这三处没有事务可言，中途失败会留下「有图有记录但检索不到」的半截数据，
    #   而且不会自愈。所以成功一步压一步进 undo 栈，出错就逆序撤销。
    student_dir = os.path.join(str(settings.FACE_IMG_DIR), student.student_no)
    os.makedirs(student_dir, exist_ok=True)
    import time
    filename = f"{int(time.time() * 1000)}.jpg"
    img_path = os.path.join(student_dir, filename)
    rel_path = f"/uploads/faces/{student.student_no}/{filename}"

    undo: list[tuple[str, object]] = []
    face = None
    face_repo_face_id = None
    try:
        # imwrite 写失败是返回 False 而不是抛异常，必须显式判断——
        # 否则磁盘满了也会静默往下走，照样建记录、照样写向量
        if not cv2.imwrite(img_path, img):
            raise OSError(f"磁盘写入失败（返回 False）: {img_path}")
        undo.append(("disk", lambda: _remove_disk_file(img_path)))

        # 先建 SQLite 记录，用自增 id 作为 Milvus 的 face_id（保证 1:1 映射）
        face = face_repo.create_face_record(db, student_id, rel_path)
        face_repo_face_id = face.id
        undo.append(("sqlite", lambda rid=face.id: _delete_face_row(db, rid)))

        face.face_id = face_repo_face_id
        db.commit()
        db.refresh(face)

        face_repo.insert_vector(face_repo_face_id, student_id, embedding)
    except Exception as exc:  # noqa: BLE001
        logger.error("[face] 录入失败，执行补偿回滚: %s", exc)
        _rollback_enroll(db, undo, face_repo_face_id)
        return {
            "code": 1,
            "message": f"录入失败，已回滚已完成步骤（{type(exc).__name__}）",
            "detail": str(exc),
        }

    # 7. update student face_status
    #    这是派生字段（可由 faces 表 count 得出），失败刻意不回滚：
    #    走到这里三处写入都已完成，录入实质是成功的，为一个冗余字段把整次录入
    #    作废代价更大；失败只记日志，留待重建脚本修正。
    try:
        student_repo.update_face_status(db, student_id, 1)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[face] face_status 更新失败（录入本身已成功，状态待重建）student=%s: %s",
            student_id, exc,
        )

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
