# -*- coding: utf-8 -*-
"""验证集阈值扫描：在既有的脏库/正常库复现库上，对 (cross_student, cluster_distance) 组合
逐格调用生产审计 audit_duplicates，报告 脏库命中 / 正常库误报 两个指标。

用途（论文 5.6 节验证集选点）：
    · 一级解集内的候选档位在验证集上逐一复核；
    · 补齐单变量行（固定 dist 变 cross、固定 cross 变 dist）；
    · 每行同时报告 正常库误报数 —— 一级判据是双指标（召回 + 零误报），
      验证集复核必须同样双指标，否则「15 > 14」的比较不完整。

做法：
    · 复用 audit_repro_dirty_normal.py 已建好的两份独立库
      （backend/.repro/<run-id>-dirty 与 <run-id>-normal），**不重新录入、不加载识别模型**，
      只读向量库后调用 audit_duplicates(thresholds...)，因此单格耗时可忽略。
    · **每个场景必须独立进程运行**：core.db / core.milvus 在首次导入时读取环境变量并
      缓存连接，同进程内切换库目录不生效（曾因此得到过两份相同的假「normal」数据）。
    · 输出 markdown 表格与 JSON 归档。

用法（在 backend 目录下，每场景各起一个进程）：
    .venv/Scripts/python.exe scripts/audit_validation_sweep.py --run-id 20261022 --scenario dirty
    .venv/Scripts/python.exe scripts/audit_validation_sweep.py --run-id 20261022 --scenario normal
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--run-id", default="20261022")
ap.add_argument("--scenario", required=True, choices=["dirty", "normal"])
ap.add_argument("--same-student", type=float, default=0.40)
args, _ = ap.parse_known_args()

# ---- 扫描格：覆盖「单变量行」与「并列下界演示行」 ----
# dist 固定 0.50 / 0.60 各扫一遍 cross（检查 dist 方向的敏感性与单变量行）
# 另补 (0.43, 0.60)：非网格点，仅作参考（不在一级解集内，不参与选点）
CELLS: list[tuple[float, float]] = [
    (0.25, 0.50), (0.30, 0.50), (0.35, 0.50), (0.40, 0.50),
    (0.45, 0.50), (0.50, 0.50), (0.55, 0.50),
    (0.25, 0.60), (0.30, 0.60), (0.35, 0.60), (0.40, 0.60),
    (0.45, 0.60), (0.50, 0.60), (0.55, 0.60),
    (0.43, 0.60),
]


def main() -> int:
    run_dir = BACKEND / ".repro" / f"{args.run_id}-{args.scenario}"
    if not (run_dir / "milvus_lite.db").exists():
        print(f"缺少复现库: {run_dir}（先运行 audit_repro_dirty_normal.py）")
        return 1

    # ---- 环境变量必须在导入 core.* 之前设置（与复现脚本同规则） ----
    os.environ["SQLITE_URL"] = "sqlite:///" + (run_dir / "attendance.db").as_posix()
    os.environ["MILVUS_DB_PATH"] = str(run_dir / "milvus_lite.db")
    os.environ["FACE_IMG_DIR"] = str((run_dir / "uploads" / "faces").as_posix())
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    sys.path.insert(0, str(BACKEND))

    from core.db import SessionLocal  # noqa: E402
    from core.milvus import get_client  # noqa: E402
    from service import cluster_service  # noqa: E402

    get_client()
    db = SessionLocal()
    rows: list[dict] = []
    try:
        for cross, dist in CELLS:
            audit = cluster_service.audit_duplicates(
                db,
                same_student_threshold=args.same_student,
                cross_student_threshold=cross,
                cluster_distance=dist,
            )
            data = audit.get("data") or {}
            s = data.get("summary", {})
            rows.append({
                "scenario": args.scenario,
                "cross": cross,
                "dist": dist,
                "face_count": data.get("face_count"),
                "duplicate_pairs": s.get("duplicate_pairs"),
                "suspicious_clusters": s.get("suspicious_cluster_count"),
                "dirty_pairs": s.get("dirty_pairs"),
            })
            print(f"[{args.scenario}] cross={cross:.2f} dist={dist:.2f} -> "
                  f"pairs={s.get('duplicate_pairs')} clusters={s.get('suspicious_cluster_count')} "
                  f"dirty={s.get('dirty_pairs')}", flush=True)
    finally:
        db.close()

    out = BACKEND / ".repro" / f"{args.run_id}-validation-sweep-{args.scenario}.json"
    out.write_text(json.dumps({
        "run_id": args.run_id,
        "scenario": args.scenario,
        "same_student": args.same_student,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 汇总（markdown）=====")
    print(f"### 场景：{args.scenario}")
    print("| cross_student | cluster_distance | duplicate_pairs | suspicious_clusters | dirty_pairs |")
    print("|---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['cross']:.2f} | {r['dist']:.2f} | {r['duplicate_pairs']} | "
              f"{r['suspicious_clusters']} | {r['dirty_pairs']} |")
    print(f"\nresult -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
