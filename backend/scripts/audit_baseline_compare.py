# -*- coding: utf-8 -*-
"""基线对照：把「抄来的默认阈值」和「标定后的部署值」放在同一份数据上比。

为什么需要这个脚本
------------------
前面做的 84 档网格扫描与验证集复核，回答的是「哪个档位最好」。
但它没有回答一个更先决的问题：**如果什么都不做、沿用常见默认值，会怎样？**

简历上写「15 对重复注册全部检出」是绝对值，读者没有参照系——15/15 到底是因为
标定做得好，还是因为这份数据本来就简单？只有把默认阈值拉到同一份数据上跑一遍，
才知道这个 15 是不是「凭空多出来的」。

本脚本固定 cluster_distance = 0.60（部署值），单变量扫 cross_student：
    · 0.80 —— 网上资料对人脸识别最常见的推荐值（针对未归一化向量 / 别的模型）；
    · 0.50 —— 本项目初版写死的值（见 10-19 那篇文章）；
    · 0.40 —— 网格扫描 + 验证集复核后的部署值；
    · 其余档位用于看清楚"从哪一档开始检得出"。

在两种库上各跑一遍：
    · dirty  脏库：含 15 对跨学号重复注册 → 统计**检出多少对**（召回）；
    · normal 正常库：干净数据 → 统计**误报多少对**（精确率）。
    两个数字必须一起看：只报召回不报误报，等于没说。

做法（与 audit_validation_sweep.py 同规则）：
    · 复用 backend/.repro/<run-id>-{dirty,normal} 两份已建好的独立库，
      不重新录入、不加载识别模型，只读向量库后调用生产审计 audit_duplicates。
    · **每个场景必须独立进程**：core.db / core.milvus 首次导入即缓存连接，
      同进程内切换库目录不生效（曾因此得到过两份相同的假「normal」数据）。
      同一进程内扫多个阈值是安全的——那只是传参，不切库。
    · 结果归档到 docs/verify-log/（**不是** .repro，那里被 gitignore，
      而这份结果是要能被简历/文章引用、能被第三方核对的）。

用法（在 backend 目录下，每场景各起一个进程）：
    .venv\\Scripts\\python.exe scripts/audit_baseline_compare.py --scenario dirty
    .venv\\Scripts\\python.exe scripts/audit_baseline_compare.py --scenario normal
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

ap = argparse.ArgumentParser()
ap.add_argument("--run-id", default="20261022")
ap.add_argument("--scenario", required=True, choices=["dirty", "normal"])
ap.add_argument("--same-student", type=float, default=0.40)
ap.add_argument("--dist", type=float, default=0.60,
                help="cluster_distance 固定为部署值，只做 cross 方向单变量扫描")
args, _ = ap.parse_known_args()

# 扫描档位：默认值 -> 部署值，中间留出足够密度看清拐点
THRESHOLDS: list[tuple[float, str]] = [
    (0.80, "网上资料最常见推荐值"),
    (0.70, ""),
    (0.60, ""),
    (0.50, "本项目初版写死值"),
    (0.45, ""),
    (0.40, "★ 网格扫描 + 验证集复核后的部署值"),
    (0.35, ""),
    (0.30, ""),
]


def main() -> int:
    run_dir = BACKEND / ".repro" / f"{args.run_id}-{args.scenario}"
    if not (run_dir / "milvus_lite.db").exists():
        print(f"缺少复现库: {run_dir}（先运行 audit_repro_dirty_normal.py）")
        return 1

    # ---- 环境变量必须在导入 core.* 之前设置 ----
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
        for cross, note in THRESHOLDS:
            audit = cluster_service.audit_duplicates(
                db,
                same_student_threshold=args.same_student,
                cross_student_threshold=cross,
                cluster_distance=args.dist,
            )
            data = audit.get("data") or {}
            s = data.get("summary", {})
            rows.append({
                "cross_student": cross,
                "cluster_distance": args.dist,
                "note": note,
                "face_count": data.get("face_count"),
                "duplicate_pairs": s.get("duplicate_pairs"),
                "suspicious_clusters": s.get("suspicious_cluster_count"),
                "dirty_pairs": s.get("dirty_pairs"),
            })
            print(f"[{args.scenario}] cross={cross:.2f} -> "
                  f"pairs={s.get('duplicate_pairs')} "
                  f"clusters={s.get('suspicious_cluster_count')} "
                  f"dirty={s.get('dirty_pairs')}  {note}", flush=True)
    finally:
        db.close()

    out_dir = REPO / "docs" / "verify-log"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"audit_baseline_{args.scenario}.json"
    out.write_text(json.dumps({
        "run_id": args.run_id,
        "scenario": args.scenario,
        "same_student": args.same_student,
        "cluster_distance": args.dist,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n===== {args.scenario}（markdown）=====")
    print("| cross_student | 检出对数 | 可疑簇数 | 同号脏数据 | 说明 |")
    print("|---:|---:|---:|---:|---|")
    for r in rows:
        print(f"| {r['cross_student']:.2f} | {r['duplicate_pairs']} | "
              f"{r['suspicious_clusters']} | {r['dirty_pairs']} | {r['note']} |")
    print(f"\nresult -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
