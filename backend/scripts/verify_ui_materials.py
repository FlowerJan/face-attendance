# -*- coding: utf-8 -*-
"""校验 UI 跑批素材在真实检索下的实际行为。

目的：`docs/ui-run-materials/` 里的照片是要交给人在浏览器里点着用的，
必须先确认每张图**实际**会触发什么结果（认出 / 未认出 / 未检出人脸 / 过不了画质门控），
否则跑批清单里写的预期就是错的。

跑法：
    cd backend
    .venv\Scripts\python.exe scripts\verify_ui_materials.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
MAT = ROOT / "docs" / "ui-run-materials"

# ★ 隔离环境：绝不能碰演示库（Milvus Lite 单进程锁 + 演示数据要留给 UI 演示）
TMP = Path(tempfile.mkdtemp(prefix="ui_mat_check_"))
os.environ["SQLITE_URL"] = "sqlite:///" + str((TMP / "a.db").as_posix())
os.environ["MILVUS_DB_PATH"] = str(TMP / "milvus.db")
os.environ["FACE_IMG_DIR"] = str(TMP / "faces")
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.enums import EnumContainer  # noqa: E402

EnumContainer.load()
from core.db import SessionLocal, init_db  # noqa: E402
from repository import face_repo, student_repo  # noqa: E402
from schemas.student import StudentCreate  # noqa: E402
from service import face_service  # noqa: E402
from service import attendance_service as svc  # noqa: E402

init_db()
from insightface.app import FaceAnalysis  # noqa: E402

app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640))

db = SessionLocal()
stu = student_repo.create(
    db,
    StudentCreate(
        student_no="T2024001",
        name="测试甲",
        gender="男",
        class_name=EnumContainer.get_classes()[0],
        major=EnumContainer.get_majors()[0],
        enroll_year=2024,
        phone="13800000000",
    ),
)
sid = stu.id

# 复刻演示库状态：alpha 的 6 张底片录到同一人名下
seed_faces = sorted((BACKEND / "uploads" / "faces" / "20240001").glob("*.jpg"))
for f in seed_faces:
    face_service.enroll_face(db, sid, f.read_bytes())
print(f"基准库：学生 {sid} 录入 {len(seed_faces)} 张底片\n")

CANDIDATES = [
    "alpha_enrolled_1793153424000.jpg",
    "alpha_new_shot.jpg",
] + [f"stranger_{i}.jpg" for i in range(1, 9)]

print(f"{'文件':<38}{'top1相似度':>10}{'余量':>9}   判定")
print("-" * 78)
for name in CANDIDATES:
    p = MAT / name
    if not p.exists():
        print(f"{name:<38}  素材不存在")
        continue
    img = cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        print(f"{name:<38}  解码失败")
        continue
    faces = app.get(img)
    if not faces:
        print(f"{name:<38}{'—':>10}{'—':>9}   未检测到人脸（UI 提示「未检测到人脸」）")
        continue
    emb = np.asarray(faces[0].embedding, dtype=np.float32)
    hits = face_repo.search_similar(emb, top_k=3, only_active=True)
    top = hits[0] if hits else None
    others = (
        face_repo.search_similar(
            emb, top_k=1, only_active=True, exclude_student_id=int(top["student_id"])
        )
        if top
        else []
    )
    margin = float(top["similarity"]) - float(others[0]["similarity"]) if others else None
    sim = float(top["similarity"])

    if sim >= svc.CHECKIN_THRESHOLD:
        verdict = "签到成功"
        if margin is not None and margin < svc.MARGIN_WARN:
            verdict += f"（但余量 {margin:.4f} < {svc.MARGIN_WARN} → 判为 ambiguous）"
    else:
        verdict = f"未识别（未达阈值 {svc.CHECKIN_THRESHOLD}）"
    print(
        f"{name:<38}{sim:>10.4f}"
        + (f"{margin:>9.4f}" if margin is not None else f"{'—':>9}")
        + f"   {verdict}"
    )

print(f"\n（阈值 CHECKIN_THRESHOLD={svc.CHECKIN_THRESHOLD} / MARGIN_WARN={svc.MARGIN_WARN}）")
print(f"临时评测库：{TMP}")
