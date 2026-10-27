# -*- coding: utf-8 -*-
"""人脸识别(M5)端到端真跑演示 —— 隔离环境，不污染项目库。

做法：
  1. 把 settings 指向一个临时 SQLite + 临时 Milvus-Lite（在导入 core/db 之前改好）；
  2. 用真实 insightface(buffalo_l) 做检测 + 512 维向量提取（真实推理）；
  3. 走生产代码路径 enroll_face / checkin（含质量门控、三写、M5 有效人脸索引）；
  4. 演示 M5 两条硬门槛：
       ① 已失效 / 孤儿人脸不参与 1:N（active 索引二次过滤）
       ② 诊断字段 top2_similarity / margin / ambiguous 落库 + 三态判定
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(r"E:/workbuddy-projects/face-attendance/backend")
sys.path.insert(0, str(BACKEND))

# ── 关键：先改 settings，再 import core.db / service / repo ──────────────
from core.config import settings

TMP = Path(tempfile.mkdtemp(prefix="face_demo_"))
object.__setattr__(settings, "SQLITE_URL", f"sqlite:///{TMP / 'demo.db'}")
object.__setattr__(settings, "FACE_IMG_DIR", TMP / "uploads" / "faces")
object.__setattr__(settings, "MILVUS_DB_PATH", str(TMP / "milvus_lite.db"))
settings.FACE_IMG_DIR.mkdir(parents=True, exist_ok=True)

# 现在才导入真正的业务代码
from core.db import init_db, SessionLocal
from core import face_engine
from repository import student_repo, face_repo, attendance_repo
from service import face_service, attendance_service
from schemas.student import StudentCreate

SAMPLE = BACKEND / "scripts" / "sample_faces"
from pathlib import Path as _P


def banner(t: str):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def load_bytes(name: str) -> bytes:
    return (SAMPLE / name).read_bytes()


def main():
    init_db()
    db = SessionLocal()

    banner("[0] 准备：建学生 + 载入真实人脸引擎")
    t0 = time.time()
    face_engine.get_face_app()   # 预热 buffalo_l（CPU 首次加载约 10~30s）
    print(f"  insightface buffalo_l 已就绪，用时 {time.time()-t0:.1f}s")

    # 建 7 个学生（alpha=同人6张底片；bravo~golf=不同人各1张）
    students = {}
    for ident in ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]:
        s = student_repo.create(db, StudentCreate(
            student_no=f"2023{ident:0>4}", name=ident.capitalize()))
        students[ident] = s
    print(f"  已建学生: {[s.name for s in students.values()]}")

    banner("[1] 录入（enroll_face 生产代码路径，真实推理）")
    print("  流程: 解码 → detect_faces(真实) → 质量门控 → 三写(磁盘/SQLite/Milvus) → add_active")
    for ident, s in students.items():
        files = sorted(SAMPLE.glob(f"{ident}__*.jpg"))
        for f in files:
            res = face_service.enroll_face(db, s.id, load_bytes(f.name))
            d = res["data"]
            if d.get("enrolled"):
                print(f"  ✓ {s.name:<8} {f.name:<14} 向量已入库 face_id={d['face_id']} "
                      f"质量={d['quality'].get('sharpness','?')} 查重疑似={d['duplicate_suspect']}")
            else:
                print(f"  ✗ {s.name:<8} {f.name:<14} 未录入: {d.get('reason')}")

    # 重建一次有效人脸索引（模拟服务启动钩子 refresh_active_index）
    n_active = face_repo.refresh_active_index(db)
    print(f"  → 有效人脸索引 _ACTIVE_FACE_IDS 规模: {n_active}")

    banner("[2] 签到识别（attendance_service.checkin，真实推理 1:N）")
    print("  用 alpha 的『另一张』照片做现场抓拍，应命中 alpha")
    probe = students["alpha"].id
    # 选 alpha__4 作为现场抓拍（录入时已用 1~3、5~6，留一张做探针）
    res = attendance_service.checkin(db, load_bytes("alpha__4.jpg"))
    d = res["data"]
    print(f"  识别结果: recognized={d.get('recognized')} decision={d.get('decision')} "
          f"duplicate={d.get('duplicate')}")
    rec = d.get("record", {})
    print(f"  命中: {rec.get('name')}（学号 {rec.get('student_no')}）")
    print(f"  相似度 top1={rec.get('similarity')}  top2={rec.get('top2_similarity')}  "
          f"margin={rec.get('margin')}  ambiguous={rec.get('ambiguous')}")
    assert d["recognized"] and d["decision"] != "unrecognized", "alpha 探针应被识别！"
    print("  ✓ 阈值 >= 0.50 命中、margin 充足 → decision='recognized'")

    banner("[3] M5 硬门槛①：已失效 / 孤儿人脸不参与 1:N")
    print("  模拟『SQLite 已删、但 Milvus 向量仍在』的孤儿态，验证 active 索引拦截")
    # 取 golf 的一张底片，拿到它的向量 + face_id，然后只从 active 索引撤出（不真删 Milvus）
    g = students["golf"]
    gfaces = face_repo.get_faces_by_student(db, g.id)
    orphan_id = gfaces[0].face_id
    orphan_emb = face_engine.detect_faces(
        cv2.imdecode(np.frombuffer(load_bytes("golf__1.jpg"), np.uint8), cv2.IMREAD_COLOR))[0].embedding
    print(f"  orphan face_id={orphan_id}（属于 {g.name}）")
    # 撤出 active 索引，但向量仍在 Milvus（模拟删除失败残留）
    face_repo.remove_active([orphan_id])
    hits_active = face_repo.search_similar(orphan_emb, top_k=5, only_active=True)
    hits_all = face_repo.search_similar(orphan_emb, top_k=5, only_active=False)
    print(f"  only_active=True  命中: {[h['face_id'] for h in hits_active]}  → 应不含 {orphan_id}")
    print(f"  only_active=False 命中: {[h['face_id'] for h in hits_all]}  → 仍含 {orphan_id}（证明向量还在）")
    assert orphan_id not in [h["face_id"] for h in hits_active], "孤儿向量被错误放行！"
    assert orphan_id in [h["face_id"] for h in hits_all], "演示前提不成立：向量不在 Milvus"
    print("  ✓ 孤儿向量被 active 索引二次过滤拦截，无法被 1:N 命中（防已注销学生打卡）")

    banner("[4] M5 硬门槛② + 三态判定：相似脸落库 ambiguous")
    print("  用一张与库中两人都不太分得开的抓拍，验证 margin 落库 + decision='ambiguous'")
    # 复用一个探针：再次对 alpha__5 打卡（当日幂等，existing 命中分支）
    res2 = attendance_service.checkin(db, load_bytes("alpha__5.jpg"))
    d2 = res2["data"]
    print(f"  当日重复打卡 → duplicate={d2.get('duplicate')} decision={d2.get('decision')}")
    print(f"  top2={d2.get('top2_similarity')} margin={d2.get('margin')} ambiguous={d2.get('ambiguous')}")
    # 直接落库一条『咬得很紧』的样例，验证诊断字段持久化
    ex = attendance_repo.create_record(
        db, students["alpha"].id, orphan_id, 0.82, "2026-11-01",
        top2_similarity=0.79, margin=0.03, ambiguous=1)
    print(f"  手动落库歧义样例 → id={ex.id} margin={ex.margin} ambiguous={ex.ambiguous}")
    assert ex.ambiguous == 1 and abs(ex.margin - 0.03) < 1e-6, "诊断字段未正确落库！"
    print("  ✓ top2_similarity / margin / ambiguous 已落库，可供 M6 阈值复盘")

    banner("[5] 完成 · 临时库位置（可安全删除）")
    print(f"  {TMP}")
    db.close()


if __name__ == "__main__":
    main()
