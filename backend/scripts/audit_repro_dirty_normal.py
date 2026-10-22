# -*- coding: utf-8 -*-
"""复现「脏库命中 / 正常库 0 误报」两个场景，产出可归档数字。

做法：
    · 每个场景跑在**独立的时间戳数据库**（不碰演示库 attendance.db / milvus_lite.db），
      因此这是一次干净的复现实验，与历史数据互不污染。
    · 脏库：用真实照片 + --split 语义（同一身份拆到不同学号），构造「一人多号」异常。
    · 正常库：每个身份只录一张、且各身份互不相同（一人一号），构造干净库。
    · 两个场景都直接调用生产审计逻辑 service.cluster_service.audit_duplicates，
      报告其输出的 duplicate_pairs / suspicious_cluster_count / students_involved /
      dirty_pairs，这正是写论文第 5 章要引用的数字。

用法（在 backend 目录下，每个场景各起一个进程）：
    .venv/Scripts/python.exe scripts/audit_repro_dirty_normal.py --scenario dirty  --run-id 20261022
    .venv/Scripts/python.exe scripts/audit_repro_dirty_normal.py --scenario normal --run-id 20261022
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# ---- 先解析场景，再据此设定独立库路径（必须在 import core.* 之前） ----
ap0 = argparse.ArgumentParser(add_help=False)
ap0.add_argument("--scenario", required=True, choices=["dirty", "normal"])
ap0.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
ap0.add_argument("--students", type=int, default=40)
args0, _ = ap0.parse_known_args()

RUN_DIR = BACKEND / ".repro" / f"{args0.run_id}-{args0.scenario}"
RUN_DIR.mkdir(parents=True, exist_ok=True)
os.environ["SQLITE_URL"] = "sqlite:///" + str((RUN_DIR / "attendance.db").as_posix())
os.environ["MILVUS_DB_PATH"] = str((RUN_DIR / "milvus_lite.db").as_posix())
os.environ["FACE_IMG_DIR"] = str((RUN_DIR / "uploads" / "faces").as_posix())
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from core.db import SessionLocal, init_db  # noqa: E402
from core.enums import EnumContainer  # noqa: E402
from core.face_engine import get_face_app  # noqa: E402
from core.milvus import get_client  # noqa: E402
from repository import face_repo, student_repo  # noqa: E402
from schemas.student import StudentCreate  # noqa: E402
from service import face_service  # noqa: E402
from service import cluster_service  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SAMPLE_DIR = BACKEND / "scripts" / "sample_faces"
FACES_PER_STUDENT_OK = True  # 同一学生可录多张（正常库按身份分组时会用到）


def make_students(db, count: int) -> list[int]:
    """生成学生，返回新建学生 id 列表（学号从 2024xxxx 顺延）。"""
    exist = {s for (s,) in db.execute(
        __import__("sqlalchemy").text("select student_no from students")
    ).all()}
    classes = EnumContainer.get_classes() or ["智造本2024-1班"]
    majors = EnumContainer.get_majors() or ["计算机科学与技术"]
    ids: list[int] = []
    i = 0
    while len(ids) < count and i < count * 20:
        i += 1
        no = f"2024{i:04d}"
        if no in exist:
            continue
        student = student_repo.create(db, StudentCreate(
            student_no=no,
            name=f"复现{args0.scenario}-{i:03d}",
            gender="男",
            class_name=classes[0],
            major=majors[0],
            enroll_year=2024,
            phone=f"1380000{i:04d}",
        ))
        ids.append(student.id)
        exist.add(no)
    return ids


def list_face_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMG_EXT)


def enroll_split(db, files: list[Path], student_ids: list[int]) -> tuple[int, int]:
    """脏库录入：每个文件 -> 一个独立学生（同一身份被拆到多个学号）。"""
    ok = fail = 0
    for path, sid in zip(files, student_ids):
        res = face_service.enroll_face(db, sid, path.read_bytes())
        d = res.get("data") or {}
        if d.get("detected"):
            ok += 1
        else:
            fail += 1
    return ok, fail


def enroll_grouped(db, files: list[Path], student_ids: list[int]) -> tuple[int, int]:
    """正常库录入：同一身份（文件名前缀__前相同）的所有照片 -> 同一个学生；
    每个身份只取第一张作为代表，保证「一人一号、各身份互不相同」。"""
    # 按身份分组，每组只取第一张
    groups: dict[str, Path] = {}
    for p in files:
        ident = p.stem.split("__")[0] if "__" in p.stem else p.stem
        groups.setdefault(ident, p)
    rep = list(groups.values())
    ok = fail = 0
    for path, sid in zip(rep, student_ids):
        res = face_service.enroll_face(db, sid, path.read_bytes())
        d = res.get("data") or {}
        if d.get("detected"):
            ok += 1
        else:
            fail += 1
    return ok, fail


def run() -> int:
    init_db()
    EnumContainer.load()
    get_client()
    app = get_face_app()
    print(f"[repro:{args0.scenario}] 向量库与模型就绪，库目录: {RUN_DIR}")

    db = SessionLocal()
    try:
        files = list_face_files(SAMPLE_DIR)
        print(f"[repro:{args0.scenario}] 素材: {len(files)} 张 -> "
              f"{len({p.stem.split('__')[0] for p in files})} 个身份")

        ids = make_students(db, args0.students)
        total_students = db.execute(__import__("sqlalchemy").text(
            "select count(*) from students")).scalar()
        print(f"[repro:{args0.scenario}] 新增学生 {len(ids)}，库内共 {total_students}")

        if args0.scenario == "dirty":
            ok, fail = enroll_split(db, files, ids)
        else:
            ok, fail = enroll_grouped(db, files, ids)
        print(f"[repro:{args0.scenario}] 录入完成: 成功 {ok}，被拒 {fail}")

        vec = len(face_repo.get_all_vectors())
        print(f"[repro:{args0.scenario}] 向量库: {vec} 条")

        audit = cluster_service.audit_duplicates(db)
        data = audit.get("data") or {}
        s = data.get("summary", {})
        print("\n===== RESULT =====")
        print(f"face_count            = {data.get('face_count')}")
        print(f"duplicate_pairs       = {s.get('duplicate_pairs')}")
        print(f"suspicious_clusters   = {s.get('suspicious_cluster_count')}")
        print(f"students_involved     = {s.get('students_involved')}")
        print(f"dirty_pairs           = {s.get('dirty_pairs')}")

        # 把结果落盘，便于归档与后续汇总
        out = RUN_DIR / "result.json"
        out.write_text(__import__("json").dumps({
            "scenario": args0.scenario,
            "run_id": args0.run_id,
            "db_dir": str(RUN_DIR),
            "summary": s,
            "thresholds": data.get("thresholds"),
            "duplicates": [
                {"student_a": d["student_a"]["student_no"],
                 "student_b": d["student_b"]["student_no"],
                 "similarity": d["similarity"]}
                for d in data.get("duplicates", [])
            ],
            "suspicious_clusters": [
                {"student_ids": c["student_ids"],
                 "face_ids": c["face_ids"]}
                for c in data.get("suspicious_clusters", [])
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[repro:{args0.scenario}] result -> {out}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(run())
