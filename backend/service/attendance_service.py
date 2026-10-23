"""Attendance service — face check-in: capture -> gate -> identify -> record.

与录入(enroll)的关键差异：
- enroll 是"注册"：门控不过就拒绝入库，一切从严；
- checkin 是"识别"：拿现场抓拍与库中向量做 1:N 检索，命中即记一次签到，
  同一学生同一天只记一条（唯一约束 + 业务层预查双重保证）。
"""
import logging
from datetime import date

import cv2
import numpy as np
from sqlalchemy.orm import Session

from core.face_engine import detect_faces
from core.quality_gate import QualityGate
from repository import attendance_repo, face_repo, student_repo

logger = logging.getLogger(__name__)

_quality_gate = QualityGate()

# 签到识别阈值（1:N 命中判定），与审计阈值不是一回事：
#   审计的 cross_student=0.40 是"脸库查重"标定产物，面向两两比对召回；
#   签到是"把抓拍判给某个人"，错判会直接写一条出勤记录（假接受代价更高），
#   因此取更保守的 0.50。注释区间参考 cluster_service：同人 0.45~0.65，异人 -0.1~0.15。
# 局限：该阈值尚未像审计阈值那样做过网格标定，属于待办（见 README 已知事项）。
CHECKIN_THRESHOLD = 0.50


def checkin(db: Session, image_bytes: bytes) -> dict:
    """Full check-in pipeline. Never raises; returns unified {code, data}."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"code": 1, "message": "invalid image data"}

    # 1. detect + embed
    faces = detect_faces(img)
    if not faces:
        return {"code": 0, "data": {"recognized": False, "reason": "no face detected"}}
    main_face = faces[0]

    # 2. 质量门控：现场图过脏（模糊/过曝/侧脸）识别不可信，拒绝并回传原因
    quality = _quality_gate.check(img, main_face, faces)
    if not quality.passed:
        return {"code": 0, "data": {
            "recognized": False,
            "reason": quality.reason,
            "rule": quality.rule,
            "quality": quality.to_dict(),
        }}

    embedding = main_face.embedding

    # 3. 1:N 检索，取最高相似度候选
    try:
        hits = face_repo.search_similar(embedding, top_k=3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[attendance] 向量检索失败: %s", exc)
        return {"code": 1, "message": "vector store unavailable"}

    best = hits[0] if hits else None
    best_similarity = float(best["similarity"]) if best else -1.0
    if best is None or best_similarity < CHECKIN_THRESHOLD:
        return {"code": 0, "data": {
            "recognized": False,
            "reason": "no match above threshold",
            "best_similarity": round(best_similarity, 4),
            "threshold": CHECKIN_THRESHOLD,
        }}

    student = student_repo.get_by_id(db, int(best["student_id"]))
    if not student:
        return {"code": 0, "data": {
            "recognized": False,
            "reason": "matched face has no student record",
            "best_similarity": round(best_similarity, 4),
        }}

    # 4. 当日去重：已签则直接返回既有记录（幂等，方便重复抓拍不报错）
    today = date.today().isoformat()
    existing = attendance_repo.get_by_student_and_date(db, student.id, today)
    if existing:
        return {"code": 0, "data": {
            "recognized": True,
            "duplicate": True,
            "record": _serialize(existing, student),
        }}

    record = attendance_repo.create_record(
        db, student.id, int(best["face_id"]), best_similarity, today
    )
    return {"code": 0, "data": {
        "recognized": True,
        "duplicate": False,
        "record": _serialize(record, student),
    }}


def list_records(db: Session, checkin_date: str, page: int, page_size: int) -> dict:
    return {"code": 0, "data": attendance_repo.list_by_date(db, checkin_date, page, page_size)}


def day_summary(db: Session, checkin_date: str) -> dict:
    return {"code": 0, "data": attendance_repo.day_summary(db, checkin_date)}


def _serialize(record, student) -> dict:
    return {
        "id": record.id,
        "student_id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "class_name": student.class_name,
        "face_id": record.face_id,
        "similarity": round(float(record.similarity), 4) if record.similarity is not None else None,
        "checkin_date": record.checkin_date,
        "checkin_at": str(record.checkin_at) if record.checkin_at else None,
    }
