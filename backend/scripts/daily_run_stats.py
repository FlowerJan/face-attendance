"""每日真实运行取数脚本 —— 一行命令拿到可写进简历的真实数据。

解决的问题：
    项目"能跑"和"在用"是两回事。简历上真正值钱的是后者，但它需要**每天记数**。
    手写 SQL 容易忘、容易断，所以做成本脚本，双击一次即出一结果行。

用法（必须用 backend/.venv，因为要用到 pymilvus / scipy）：
    cd /d E:\\workbuddy-projects\\face-attendance\\backend
    .venv\\Scripts\\python.exe scripts\\daily_run_stats.py
    .venv\\Scripts\\python.exe scripts\\daily_run_stats.py --append   # 追加写入每日清单.md

⚠️ 关于 Milvus Lite 的单进程文件锁（重要）：
    Milvus Lite 同一目录只能被一个进程打开。后端跑着的时候，
    本脚本**直连 Milvus 会报 DataDirLockedError**。
    因此本脚本采用「双模式自适应」：
      · 后端已在 8000 端口运行 → 走 HTTP 接口取数（不碰 Milvus 文件，无需停后端）
      · 后端未运行           → 直连 SQLite + Milvus（不依赖服务）
    无论哪种模式都不需要手动停后端。

输出：
    · 学生总数 / 已录入人脸人数 / 录入覆盖率
    · SQLite 人脸元数据条数 vs Milvus 向量条数（不一致就是孤儿数据）
    · 脸库审计结果：聚类簇数、疑似重复注册、脏数据、跨学生混合簇
    · 一行 markdown 表格行，可直接粘进「每日清单.md」的记录表
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

# backend/scripts/daily_run_stats.py -> backend/
BACKEND = Path(__file__).resolve().parent.parent

TODAY = date.today().isoformat()

# 每日清单.md 的位置：项目根目录
DAILY_LOG = BACKEND.parent / "每日清单.md"

# 后端地址，可用环境变量覆盖
API_BASE = os.environ.get("FACE_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_TIMEOUT = float(os.environ.get("FACE_API_TIMEOUT", "8"))


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


# --------------------------------------------------------------------------
# HTTP 模式：后端已启动时走接口，避免 Milvus Lite 文件锁冲突
# --------------------------------------------------------------------------

def http_get(path: str) -> dict | None:
    """GET 一个后端接口并返回 data 字段；不可用时返回 None。"""
    url = f"{API_BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=API_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def backend_alive() -> bool:
    return http_get("/api/health") is not None


def collect_over_http() -> dict | None:
    """后端在跑时，用接口拼出与直连模式等价的统计结果。"""
    summary = http_get("/api/stats/summary")
    coverage = http_get("/api/stats/face-coverage")
    counts = http_get("/api/stats/face-count")
    audit = http_get("/api/audit/duplicates")
    if summary is None or coverage is None:
        return None

    total = summary.get("total_students", 0)
    enrolled = coverage.get("enrolled", 0)
    zero = counts.get("zero_face", 0) if counts else 0
    one = counts.get("one_face", 0) if counts else 0
    multiple = counts.get("multiple_faces", 0) if counts else 0
    orphans = counts.get("orphan_faces", 0) if counts else 0
    # faces 元数据条数 = 一张脸的学生数 + 多张脸的学生数（每人至少一张）
    face_rows = one + multiple

    audit_summary = (audit or {}).get("summary", {}) or {}

    return {
        "mode": "http",
        "students": total,
        "faces": face_rows,
        "enrolled": enrolled,
        "coverage": round(enrolled / total * 100, 1) if total else 0.0,
        "vectors": face_rows,   # 接口口径下向量数与 faces 行数同源
        "vectors_known": False,
        "orphans": orphans,
        "audit": {
            "cluster_count": audit_summary.get("cluster_count", 0),
            "duplicate_pairs": audit_summary.get("duplicate_pairs", 0),
            "dirty_pairs": audit_summary.get("dirty_pairs", 0),
            "suspicious_cluster_count": audit_summary.get("suspicious_cluster_count", 0),
            "students_involved": audit_summary.get("students_involved", 0),
        },
    }



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


def collect_direct() -> dict:
    """后端未运行时，直连 SQLite + Milvus 取数。"""
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

    students = counts.get("students", 0)
    enrolled = counts.get("enrolled", 0)
    return {
        "mode": "direct",
        "students": students,
        "faces": counts.get("faces", 0),
        "enrolled": enrolled,
        "coverage": round(enrolled / students * 100, 1) if students else 0.0,
        "vectors": vector_count,
        "vectors_known": vector_count > 0,
        "orphans": 0,
        "audit": {
            "cluster_count": summary.get("cluster_count", 0),
            "duplicate_pairs": summary.get("duplicate_pairs", 0),
            "dirty_pairs": summary.get("dirty_pairs", 0),
            "suspicious_cluster_count": summary.get("suspicious_cluster_count", 0),
            "students_involved": summary.get("students_involved", 0),
        },
    }


def print_http_result(result: dict) -> None:
    """HTTP 模式的结果展示。"""
    print(f"[HTTP] 后端 {API_BASE} 正在运行，走接口取数（不触碰 Milvus 文件锁）")
    print(f"  学生总数              {result['students']}")
    print(f"  已录入人脸人数        {result['enrolled']}")
    print(f"  录入覆盖率            {result['coverage']}%\n")
    audit = result.get("audit", {})
    if audit:
        print("[审计] GET /api/audit/duplicates")
        print(f"  聚类簇数              {audit.get('cluster_count', 0)}")
        print(f"  疑似重复注册(跨学号)  {audit.get('duplicate_pairs', 0)}")
        print(f"  脏数据(同学号低相似)  {audit.get('dirty_pairs', 0)}")
        print(f"  跨学生混合簇          {audit.get('suspicious_cluster_count', 0)}")
        print(f"  涉及学生数            {audit.get('students_involved', 0)}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true",
                    help="把结果追加写入项目根目录的「每日清单.md」（同一天只保留一行）")
    args = ap.parse_args()

    _boot_imports()
    print_header()

    # 双模式自适应：后端在跑就走 HTTP（不碰 Milvus 文件锁），否则直连
    result = collect_over_http() if backend_alive() else None
    if result is not None:
        print_http_result(result)
    else:
        result = collect_direct()

    prev = read_prev_vectors()
    row = build_row(result, prev)
    print("=== 记录表行 ===")
    print(row)

    if args.append:
        path = append_to_log(result)
        print(f"[ok] 已写入 {path}")
    else:
        print("（加 --append 可自动写入「每日清单.md」）")

    return 0



# --------------------------------------------------------------------------
# 记录表：追加写入「每日清单.md」
# --------------------------------------------------------------------------

TABLE_HEADER = (
    "| 日期 | 已录入人数 | 向量数 | 新增录入 | 异常数 | 备注 |\n"
    "|---|---:|---:|---:|---:|---|\n"
)


def read_prev_vectors() -> int | None:
    """从「每日清单.md」读取上一条记录的向量数，用于算当日新增。

    读不到（首行、文件损坏、格式不符）返回 None，调用方据此留空。
    """
    if not DAILY_LOG.exists():
        return None
    try:
        text = DAILY_LOG.read_text(encoding="utf-8")
    except OSError:
        return None

    rows = [ln for ln in text.split("\n")
            if ln.startswith("| 20") and "|" in ln]
    for ln in rows:
        if ln.startswith(f"| {TODAY} |"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) >= 3:
            try:
                return int(cells[2])
            except ValueError:
                continue
    return None


def build_row(result: dict, prev_vectors: int | None = None) -> str:
    """把统计结果渲染成一行 markdown 表格行。"""
    audit = result.get("audit", {}) or {}
    anomalies = audit.get("duplicate_pairs", 0) + audit.get("dirty_pairs", 0)
    vectors = result.get("vectors", 0)
    if prev_vectors is None:
        added = "-"
    else:
        delta = vectors - prev_vectors
        added = f"+{delta}" if delta > 0 else str(delta)
    return (f"| {TODAY} | {result.get('enrolled', 0)} | {vectors} | "
            f"{added} | {anomalies} | {result.get('note', '')} |")


def append_to_log(result: dict) -> Path | None:
    """把今日结果追加进「每日清单.md」，同一天重复跑只覆盖当天那一行。"""
    path = DAILY_LOG
    prev = read_prev_vectors()
    row = build_row(result, prev)

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ("# 每日运行记录\n\n"
                "> 由 `backend/scripts/daily_run_stats.py --append` 自动维护。\n\n")

    if TABLE_HEADER not in text:
        # 首次写入：追加记录表小节
        if not text.endswith("\n"):
            text += "\n"
        text += "\n## 每日取数记录\n\n" + TABLE_HEADER

    lines = text.rstrip("\n").split("\n")
    kept = [ln for ln in lines if not ln.startswith(f"| {TODAY} |")]
    # 找到表头行的位置，把新行插在表头之后
    out: list[str] = []
    inserted = False
    for ln in kept:
        out.append(ln)
        if not inserted and ln.strip() == TABLE_HEADER.split("\n")[1].strip():
            out.append(row)
            inserted = True
    if not inserted:
        out.append(row)

    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return path



if __name__ == "__main__":
    raise SystemExit(main())
