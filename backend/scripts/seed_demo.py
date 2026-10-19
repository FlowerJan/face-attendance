"""种子数据脚本：批量造学生 + （可选）批量录入脸照

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/seed_demo.py --students 40
    .venv/Scripts/python.exe scripts/seed_demo.py --students 40 --faces-from scripts/sample_faces
    .venv/Scripts/python.exe scripts/seed_demo.py --drop          # 先清空再种数据

说明：
- 直接调用 service / repository 层（不走 HTTP），因此不需要先启动后端；
- 用真实照片目录录入时，真人检测会正常通过；同一人的多张照片若录到不同学号，
  会被 /api/audit/duplicates 的聚类审计识别为「重复注册」——正好用来演示审计功能。
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import SessionLocal, init_db  # noqa: E402
from core.enums import EnumContainer  # noqa: E402
from core.milvus import get_client  # noqa: E402
from repository import face_repo, student_repo  # noqa: E402
from schemas.student import StudentCreate  # noqa: E402
from service import face_service  # noqa: E402

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["浩然", "雨桐", "梓涵", "俊杰", "晓彤", "嘉怡", "子轩", "思远", "梦琪", "泽宇",
         "可欣", "明轩", "静怡", "宇航", "欣然", "晨曦", "佳琪", "文博", "语嫣", "博文",
         "佳怡", "宇轩", "悦然", "浩宇", "锦程", "若曦", "天佑", "舒畅", "亦凡", "雅琳"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def make_students(db, count: int) -> list[int]:
    """生成学生，返回新建学生的 id 列表。学号从 2024xxxx 起顺延，已存在则跳过。"""
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
            name=random.choice(SURNAMES) + random.choice(GIVEN),
            gender=random.choice(["男", "女"]),
            class_name=random.choice(classes),
            major=random.choice(majors),
            enroll_year=random.choice([2023, 2024]),
            phone=f"13{random.randint(100000000, 999999999)}",
        ))
        ids.append(student.id)
        exist.add(no)
    return ids


def enroll_from_dir(db, folder: Path, max_students: int) -> tuple[int, int, list[str]]:
    """把目录里的照片逐张录给不同学生（一人一张），返回 (成功, 被拒, 详情)。"""
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMG_EXT)
    if not files:
        return 0, 0, []
    # 取还没有脸照的学生
    rows = db.execute(__import__("sqlalchemy").text(
        "select id, student_no from students where face_status = 0 order by id limit :n"
    ), {"n": max_students}).all()
    if not rows:
        return 0, 0, []

    ok_count = fail_count = 0
    details: list[str] = []
    for path, (sid, student_no) in zip(files, rows):
        result = face_service.enroll_face(db, sid, path.read_bytes())
        data = result.get("data") or {}
        if data.get("detected"):
            ok_count += 1
            details.append(f"  {student_no} <- {path.name}")
        else:
            fail_count += 1
            details.append(f"  {student_no} x  {path.name} ({data.get('reason') or result.get('message')})")
    return ok_count, fail_count, details


def drop_all() -> None:
    """清空 SQLite 表 + Milvus 集合 + 上传目录。"""
    import shutil

    from core.config import settings
    from core.milvus import drop_collection

    from core.db import engine
    from models import face, notice, student  # noqa: F401
    from core.db import Base

    Base.metadata.drop_all(bind=engine)
    drop_collection()
    if settings.FACE_IMG_DIR.exists():
        shutil.rmtree(settings.FACE_IMG_DIR, ignore_errors=True)
        settings.FACE_IMG_DIR.mkdir(parents=True, exist_ok=True)
    print("[seed] 已清空数据库 / 向量库 / 上传目录")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=40, help="生成学生数量")
    parser.add_argument("--faces-from", type=str, default=None, help="用指定目录的真实照片录入")
    parser.add_argument("--max-enroll", type=int, default=20, help="最多给多少名学生录入脸照")
    parser.add_argument("--drop", action="store_true", help="先清空再种数据")
    args = parser.parse_args()

    if args.drop:
        drop_all()

    init_db()
    EnumContainer.load()
    get_client()
    print("[seed] 向量库已就绪")

    db = SessionLocal()
    try:
        new_ids = make_students(db, args.students)
        total = db.execute(__import__("sqlalchemy").text("select count(*) from students")).scalar()
        print(f"[seed] 新增学生 {len(new_ids)} 人，库内共 {total} 人")

        if args.faces_from:
            folder = Path(args.faces_from)
            if not folder.is_absolute():
                folder = Path(__file__).resolve().parent.parent / folder
            if not folder.exists():
                print(f"[seed] 目录不存在: {folder}")
                return 1

            from core.face_engine import get_face_app
            try:
                get_face_app()
                print("[seed] 人脸模型已加载")
            except Exception as exc:  # noqa: BLE001
                print(f"[seed] 人脸模型不可用: {exc}")
                print("[seed] 请先执行：python scripts/download_model.py")
                return 1

            ok_count, fail_count, details = enroll_from_dir(db, folder, args.max_enroll)
            print(f"[seed] 真实照片录入：成功 {ok_count}，被拒 {fail_count}")
            for line in details:
                print(line)

        cov = db.execute(__import__("sqlalchemy").text(
            "select sum(case when face_status=1 then 1 else 0 end), count(*) from students"
        )).one()
        vec = len(face_repo.get_all_vectors())
        print(f"[seed] 完成：已录入 {cov[0]}/{cov[1]} 人，向量库 {vec} 条")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
