"""每日真实运行取数脚本 —— 一行命令拿到可写进简历的真实数据。

解决的问题：
    项目"能跑"和"在用"是两回事。简历上真正值钱的是后者，但它需要**每天记数**。
    手写 SQL 容易忘、容易断，所以做成本脚本，双击一次即出一结果行。

用法（必须用 backend/.venv，因为要用到 pymilvus / scipy）：
    cd /d E:\\workbuddy-projects\\face-attendance\\backend
    .venv\\Scripts\\python.exe scripts\\daily_run_stats.py

⚠️ 前置条件 —— Milvus Lite 是**单进程文件锁**：
    后端跑着的时候本脚本会报 DataDirLockedError。先停后端：
        netstat -ano | findstr :8000
        taskkill /F /PID <pid>

输出：
    · 学生总数 / 已录入人脸人数 / 录入覆盖率
    · SQLite 人脸元数据条数 vs Milvus 向量条数（不一致就是孤儿数据）
    · 脸库审计结果：聚类簇数、疑似重复注册、脏数据、跨学生混合簇
    · 一行 markdown 表格行，可直接粘进「每日清单.md」的记录表
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

# backend/scripts/daily_run_stats.py -> backend/
BACKEND = Path(__file__).resolve().parent.parent

TODAY = date.today().isoformat()


def _boot_imports() -> None:
    """把 backend 挂进 sys.path 并切换工作目录。

    core.config 里 BASE_DIR 用相对解析，不切目录会算错 db / milvus 路径。
    """
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读方式打开 SQLite —— 取数脚本绝不应对演示数据产生副作用。"""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def print_header() -> None:
    print(f"=== face-attendance 每日真实运行取数 · {TODAY} ===\n")


def collect_counts() -> dict[str, int]:
    """统计 SQLite 台账：学生数、人脸数、已录入人数。"""
    db_path = BACKEND / "attendance.db"
    if not db_path.exists():
        print("[warn] 找不到 attendance.db，是否已执行数据库初始化？\n")
        return {}

    with _connect_ro(db_path) as con:
        students = con.execute("select count(*) from students").fetchone()[0]
        faces = con.execute("select count(*) from faces").fetchone()[0]
        enrolled = con.execute(
            "select count(distinct student_id) from faces").fetchone()[0]

    print("[SQLite] attendance.db")
    print(f"  学生总数              {students}")
    print(f"  人脸元数据条数        {faces}")
    print(f"  已录入人脸人数        {enrolled}")
    coverage = (enrolled / students * 100) if students else 0.0
    print(f"  录入覆盖率            {coverage:.1f}%\n")
    return {"students": students, "faces": faces, "enrolled": enrolled}


def collect_vectors() -> int:
    """读 Milvus 全量向量，返回条数；失败返回 0 并给出排障提示。"""
    try:
        from repository import face_repo  # noqa: PLC0415
        return len(face_repo.get_all_vectors())
    except Exception as exc:  # noqa: BLE001
        print("[Milvus] 读取失败")
        print(f"  {type(exc).__name__}: {exc}")
        print("  → 多半是后端没停，抢了 Milvus Lite 的单进程文件锁。")
        print("    netstat -ano | findstr :8000  然后 taskkill /F /PID <pid>\n")
        return 0


def run_audit() -> dict[str, int]:
    """跑一次脸库审计，等价于 GET /api/audit/duplicates（但不用起服务）。"""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.config import settings
        from service import cluster_service

        engine = create_engine(settings.SQLITE_URL, future=True)
        Session = sessionmaker(bind=engine, future=True)
        with Session() as db:
            result = cluster_service.audit_duplicates(db)
        return result.get("data", {}).get("summary", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] 失败: {type(exc).__name__}: {exc}\n")
        return {}


def main() -> int:
    _boot_imports()
    print_header()

    counts = collect_counts()
    vector_count = collect_vectors()

    if vector_count:
        print("[Milvus] 向量库")
        print(f"  向量总数              {vector_count}")
        faces = counts.get("faces", 0)
        if faces and vector_count != faces:
            print(f"  ⚠️  孤儿数据：SQLite {faces} 条 vs 向量 {vector_count} 条，"
                  f"差 {faces - vector_count} 条")
            print("     这类不一致正是 Interview 准备清单 2.1 里那个问题的实证。\n")
        else:
            print("  ✅ 与 SQLite 条数一致，无孤儿\n")

    summary: dict[str, int] = {}
    if vector_count >= 2:
        summary = run_audit()
        if summary:
            print("[审计] GET /api/audit/duplicates")
            print(f"  聚类簇数              {summary.get('cluster_count', 0)}")
            print(f"  疑似重复注册(跨学号)  {summary.get('duplicate_pairs', 0)}")
            print(f"  脏数据(同学号低相似)  {summary.get('dirty_pairs', 0)}")
            print(f"  跨学生混合簇          {summary.get('suspicious_cluster_count', 0)}")
            print(f"  涉及学生数            {summary.get('students_involved', 0)}\n")
    else:
        print("[审计] 向量不足 2 条，跳过\n")

    dup = summary.get("duplicate_pairs", 0)
    dirty = summary.get("dirty_pairs", 0)
    print("=== 粘进「每日清单.md」第六节记录表 ===")
    print(f"| {TODAY} | {counts.get('enrolled', 0)} | {vector_count} | "
          f"- | {dup + dirty} |  |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
