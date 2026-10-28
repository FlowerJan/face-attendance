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

# 识别余量告警线：第一名与「另一个人」的相似度差距小于该值时，
# 说明画面里这个人跟库里两个人都不太分得开，判给谁都不可靠。
#   过阈值只证明"像某个人"，不证明"只像这一个人"——
#   长相相近 / 同一人多次录入时，top1 与 top2 会咬得很紧，
#   此时 0.50 这道闸形同虚设（两个都过），必须单独提示人工复核。
# 取 0.10：与审计标定的异人上限 0.15 同源，留一点余量不与之重叠。
# 注意：不因此拒绝签到——出勤现场拒绝的代价（漏签）大于存疑记录的代价，
#   只在返回里标记 ambiguous，由教师端决定是否复核。
MARGIN_WARN = 0.10


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
    # 识别余量要拿「另一个人」的最高相似度来算，必须在 Milvus 层排除 top1 的学号，
    # 而不是取回 top3 后在窗口里找——同一学生常录多张底片，top3 可能全是他自己，
    # 那样「另一个人」根本不在窗口内，余量会恒为 None、歧义检测静默失效。
    # top2_similarity 仍取窗口内第二名（无论是否同一人），用于 top1-top2 紧挨度复盘。
    top2_similarity = float(hits[1]["similarity"]) if len(hits) >= 2 else None
    margin = _identify_margin(embedding, best)
    ambiguous = bool(_margin_fields(margin).get("ambiguous"))
    if best is None or best_similarity < CHECKIN_THRESHOLD:
        return {"code": 0, "data": {
            "recognized": False,
            "decision": "unrecognized",
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
            "decision": "ambiguous" if ambiguous else "recognized",
            "duplicate": True,
            "top2_similarity": round(top2_similarity, 4) if top2_similarity is not None else None,
            "record": _serialize(existing, student),
            **_margin_fields(margin),
        }}

    record = attendance_repo.create_record(
        db, student.id, int(best["face_id"]), best_similarity, today,
        top2_similarity=top2_similarity,
        margin=margin,
        ambiguous=int(ambiguous),
    )
    return {"code": 0, "data": {
        "recognized": True,
        "decision": "ambiguous" if ambiguous else "recognized",
        "duplicate": False,
        "top2_similarity": round(top2_similarity, 4) if top2_similarity is not None else None,
        "record": _serialize(record, student),
        **_margin_fields(margin),
    }}


def _identify_margin(embedding: np.ndarray, best: dict | None) -> float | None:
    """识别余量 = top1 相似度 − 与 top1 **不同人**的最高相似度。

    为什么要到 Milvus 层排除 top1 的学号再检索：
        同一学生录入多张时，「取回 top3 再在窗口里找异人」会失效——
        top3 窗口很可能被 top1 自己的底片占满，「另一个人」根本不在窗口内，
        余量会恒为 None，歧义检测就此静默失效（正是它最该生效的场景：长相相近 / 一人多号）。
        所以这里用 filter=student_id != top1.student_id 重新检索一次，
        直接拿到「库里另一个人"的最高相似度。

    Returns:
        None 表示库里只有一个人的底片（或检索失败），无从比较（不做歧义判定）。
    """
    if not best:
        return None
    top_student = best.get("student_id")
    top_similarity = float(best["similarity"])
    try:
        others = face_repo.search_similar(
            embedding, top_k=1, exclude_student_id=int(top_student))
    except Exception as exc:  # noqa: BLE001
        # 余量算不出来不应阻断签到：宁可少一道提示，也不能让识别整体失败。
        logger.warning("[attendance] 余量计算失败（按无歧义处理）: %s", exc)
        return None
    if not others:
        return None
    return round(top_similarity - float(others[0]["similarity"]), 4)


def _margin_fields(margin: float | None) -> dict:
    """把余量转成 API 字段：可疑时额外给出可直接展示的提示语。"""
    if margin is None:
        return {"margin": None, "ambiguous": False}
    if margin >= MARGIN_WARN:
        return {"margin": margin, "ambiguous": False}
    return {
        "margin": margin,
        "ambiguous": True,
        "warning": f"识别余量偏小（{margin:.4f} < {MARGIN_WARN}），"
                   f"存在相似人脸，建议人工复核",
    }


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
        "top2_similarity": round(float(record.top2_similarity), 4) if record.top2_similarity is not None else None,
        "margin": round(float(record.margin), 4) if record.margin is not None else None,
        "ambiguous": bool(record.ambiguous),
        "checkin_date": record.checkin_date,
        "checkin_at": str(record.checkin_at) if record.checkin_at else None,
    }
