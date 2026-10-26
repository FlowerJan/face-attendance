"""SQLite 并发写对照：默认配置 vs WAL —— 回答「单文件数据库扛不扛得住并发写」。

背景：本项目 SQLite 与 Milvus-Lite 都是单文件形态，压测脚本 bench_api.py 报的
348 QPS 是**读为主**的业务接口（学生分页 / 当日汇总 / 健康检查）。写路径是另一回事：
SQLite 默认用 rollback journal，**同一时刻只允许一个写事务**，其余并发写会立刻抛
`database is locked`（SQLite 不像 MySQL 那样排队等待锁）。这个问题在读压测里完全暴露不出来。

本脚本用真实 ORM 模型（Student + Attendance）在**临时库**上做对照：
    default：journal_mode=DELETE（出厂默认）、busy_timeout=0  —— 并发写直接失败
    wal    ：journal_mode=WAL、busy_timeout=5000、synchronous=NORMAL

为什么用临时库而不是打真实 API：
1) 不污染演示数据（并发插入会写进 attendance.db，删不干净）；
2) 隔离变量——只改 PRAGMA，排除网络与序列化开销；
3) 任何人在自己机器上都能复现，不需要先起后端。

用法（不需要起后端，但注意别和运行中的后端抢同一个库文件 —— 这里用的是临时库，不冲突）：
    .venv\\Scripts\\python.exe scripts\\bench_sqlite_write.py
    .venv\\Scripts\\python.exe scripts\\bench_sqlite_write.py --writers 50 --per-writer 20
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 以脚本方式运行时 sys.path[0] 是 scripts/，补上 backend/ 才能 import core / models
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import NullPool, create_engine, event
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models import student as student_model  # noqa: F401  注册 ORM
from models import attendance as attendance_model  # noqa: F401


def make_engine(db_path: Path, wal: bool, pooled: bool = True):
    """按场景建引擎。

    wal   ：是否开 WAL + busy_timeout + synchronous=NORMAL
    pooled：是否用连接池。False = NullPool，每个 Session 独占一条连接，
            用来模拟「连接数不受池限制」的情形（多进程 / 多实例 / 池被打满）。
            这一档才会真正把 SQLite「同一时刻只允许一个写事务」的硬约束打出来。
    """
    kwargs = {"echo": False, "connect_args": {"check_same_thread": False}}
    if not pooled:
        kwargs["poolclass"] = NullPool

    engine = create_engine(f"sqlite:///{db_path}", **kwargs)

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
            if wal:
                # WAL：读写不再互斥，允许「一写多读」并发
                cur.execute("PRAGMA journal_mode=WAL")
                # busy_timeout：拿不到写锁时轮询等待 5s 而不是立刻报错
                cur.execute("PRAGMA busy_timeout=5000")
                # NORMAL：WAL 下已能保证崩溃一致性，不必每次提交都 fsync
                cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    return engine


def _read_journal_mode(engine):
    with engine.connect() as c:
        return c.exec_driver_sql("PRAGMA journal_mode").scalar()


def seed(engine, n_students: int) -> int:
    """先落几条学生，attendance.student_id 有外键约束。"""
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        first = None
        for i in range(n_students):
            st = student_model.Student(
                student_no=f"BENCH{i:04d}", name=f"压测{i}", gender="男",
                class_name="压测班", major="压测", enroll_year=2026, face_status=0,
            )
            s.add(st)
        s.commit()
        first = s.query(student_model.Student.id).order_by(student_model.Student.id).first()[0]
    return first


def _one_write(Session, student_id: int, day_offset: int) -> tuple[bool, float, str]:
    """一次「真实写入路径」：读改写（查当日是否已签）→ 插入 → 提交。"""
    d = (date(2020, 1, 1) + timedelta(days=day_offset)).isoformat()
    t0 = time.perf_counter()
    try:
        with Session() as s:
            # 签到业务层会先查当日是否已签（幂等预查），这是真实的读改写
            exists = (
                s.query(attendance_model.Attendance.id)
                .filter(attendance_model.Attendance.student_id == student_id,
                        attendance_model.Attendance.checkin_date == d)
                .first()
            )
            if exists:
                return True, time.perf_counter() - t0, ""
            s.add(attendance_model.Attendance(
                student_id=student_id, face_id=1,
                similarity=0.83, checkin_date=d,
            ))
            s.commit()
        return True, time.perf_counter() - t0, ""
    except Exception as e:  # noqa: BLE001  压测要把所有失败都记账
        return False, time.perf_counter() - t0, type(e).__name__ + ": " + str(e)[:80]


def run_scenario(label: str, wal: bool, writers: int, per_writer: int, tmpdir: Path,
                 pooled: bool = True) -> dict:
    db_path = tmpdir / f"bench_{'wal' if wal else 'default'}_{'pool' if pooled else 'null'}.db"
    engine = make_engine(db_path, wal, pooled)
    first_id = seed(engine, writers)          # 每个写线程用一个专属 student，避免撞唯一约束
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    tasks = [(wid, k) for wid in range(writers) for k in range(per_writer)]
    latencies, errors, samples = [], [], []
    t_start = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=writers) as pool:
        futs = [pool.submit(_one_write, Session, first_id + wid, k)
                for wid, k in tasks]
        for f in futures.as_completed(futs):
            ok, dt, msg = f.result()
            latencies.append(dt)
            if not ok:
                errors.append(msg)
                if len(samples) < 3:
                    samples.append(msg)
    wall = time.perf_counter() - t_start

    ok_n = len(latencies) - len(errors)
    row = {
        "场景": label,
        "journal_mode": _read_journal_mode(engine),
        "并发写线程": writers,
        "总写事务": len(tasks),
        "成功": ok_n,
        "失败": len(errors),
        "失败率": round(len(errors) / max(1, len(tasks)) * 100, 1),
        "P50(ms)": round(statistics.median(latencies) * 1000, 1),
        "P95(ms)": round(sorted(latencies)[min(len(latencies) - 1, int(0.95 * len(latencies)))] * 1000, 1),
        "最大(ms)": round(max(latencies) * 1000, 1),
        "写吞吐(TPS)": round(ok_n / wall, 1),
        "连接池": "QueuePool（默认池）" if pooled else "NullPool（不复用连接）",
        "错误样例": samples,
    }
    print(f"\n=== {label} ===")
    print(f"  journal_mode = {row['journal_mode']} ｜ 连接池 = {row['连接池']}")
    print(f"  {row['总写事务']} 次写事务 / {writers} 并发 ｜ 成功 {ok_n} 失败 {len(errors)}"
          f"（失败率 {row['失败率']}%）")
    print(f"  P50 {row['P50(ms)']} ms ｜ P95 {row['P95(ms)']} ms ｜ 最大 {row['最大(ms)']} ms"
          f" ｜ 写吞吐 {row['写吞吐(TPS)']} TPS")
    if samples:
        print(f"  错误样例：{samples[0]}")
    engine.dispose()   # 归还连接，否则 Windows 上临时库文件删不掉
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writers", type=int, default=50, help="并发写线程数")
    ap.add_argument("--per-writer", type=int, default=10, help="每线程写事务数")
    args = ap.parse_args()

    print(f"SQLite 并发写对照（writers={args.writers}, txns={args.writers * args.per_writer}）")
    print(f"Python {sys.version.split()[0]} ｜ SQLAlchemy {__import__('sqlalchemy').__version__}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        W, N = args.writers, args.per_writer
        scenarios = [
            run_scenario("A · 出厂默认：rollback journal + 连接池", False, W, N, tmp, True),
            run_scenario("B · WAL + busy_timeout=5s + 连接池", True, W, N, tmp, True),
            run_scenario("C · 出厂默认 + NullPool（连接不受池限制）", False, W, N, tmp, False),
            run_scenario("D · WAL + busy_timeout=5s + NullPool", True, W, N, tmp, False),
        ]

        print("\n=== 汇总（50 并发写，同一批 SQLAlchemy ORM 写路径）===")
        print(f"{'场景':<40}{'失败率':>8}{'P95(ms)':>10}{'写吞吐(TPS)':>12}")
        for r in scenarios:
            print(f"{r['场景']:<40}{str(r['失败率']) + '%':>8}{r['P95(ms)']:>10.1f}"
                  f"{r['写吞吐(TPS)']:>12.1f}")
        print()
        print(f"  连接池保护下（A→B）：P95 {scenarios[0]['P95(ms)']} → {scenarios[1]['P95(ms)']} ms，"
              f"吞吐 {scenarios[0]['写吞吐(TPS)']} → {scenarios[1]['写吞吐(TPS)']} TPS；两者都不报错，"
              f"代价体现在 P95")
        print(f"  连接不受限时（C→D）：失败率 {scenarios[2]['失败率']}% → {scenarios[3]['失败率']}%，"
              f"P95 {scenarios[2]['P95(ms)']} → {scenarios[3]['P95(ms)']} ms")

        out_dir = Path(__file__).resolve().parent.parent.parent / "docs" / "verify-log"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "bench_sqlite_write.json"
        payload = {
            "writers": args.writers,
            "per_writer": args.per_writer,
            "scenarios": scenarios,
        }
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
