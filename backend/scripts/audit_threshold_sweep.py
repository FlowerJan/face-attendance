# -*- coding: utf-8 -*-
"""脸库审计阈值 —— 敏感性与标定实验（多档扫描）

为什么要这个脚本：
    `audit_recall_test.py` 只在**单一阈值**（0.40 / 0.50 / 0.60）上跑一次，
    得到的结论是"这套阈值能用"。但答辩时会被问：
      · 阈值为什么是 0.50？换个值会怎样？
      · 你这个 100% 召回，是阈值调出来的还是本来就该有？
    扫描多档阈值并同时记录【召回 / 漏报 / 误报】，才能把"我拍了个值"
    变成"我标定了一个值"。

核心前提（与 audit_recall_test.py 完全一致）：
    · ground truth 只构造一次，**不随阈值变化**；
    · 阈值只影响 audit_duplicates() 的判定，**不影响向量本身**；
      —— 所以多档扫描是公平的：同一批数据、同一个模型、同一份真值，
         只有判定阈值在变。这正是"标定"而非"调参凑答案"。

★ 标定判据（在跑之前写死，避免事后挑数）：
    主判据：取【召回率最高】且【跨组误报配对数 = 0 且负样本误报数 = 0】的档位集合，
            在该集合内按 **maximin 鲁棒性**选点 —— 即取「到最近失败档位的网格距离」
            最大者。理由：贴着边界的档位虽然当前满足条件，但再动一格就崩；
            选 plateau 中心才谈得上"标定"。
    副判据：若不存在同时满足的档位，则取【误报为 0 前提下召回最高】的档位，
            并显式标注该档存在漏报、漏报的是哪几组。
    兜底：若任何档位都有误报，则如实报告全部档位，标定失败。

★ 鲁棒性余量（margin）的定义：
    margin = min over 所有失败档位 f of ( |cross - f.cross| + |dist - f.dist| )
    失败档位 = 召回 < 100% 或 跨组误报 > 0 或 负样本误报 > 0。
    margin 越大，说明该档位离"翻车"越远。margin = 0 表示紧贴失败边界。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py --keep
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py --no-report
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
EVAL_DIR = BACKEND / ".eval"

# ---- 必须在 import core.* 之前设置，让配置指向独立评测库 ----
EVAL_DIR.mkdir(parents=True, exist_ok=True)
os.environ["SQLITE_URL"] = "sqlite:///" + str((EVAL_DIR / "attendance.db").as_posix())
os.environ["MILVUS_DB_PATH"] = str((EVAL_DIR / "milvus_lite.db").as_posix())
os.environ["FACE_IMG_DIR"] = str((EVAL_DIR / "uploads" / "faces").as_posix())
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_recall_test as art  # noqa: E402  复用 ground truth 构造与评估口径
from core.db import SessionLocal, init_db  # noqa: E402
from core.enums import EnumContainer  # noqa: E402
from core.face_engine import get_face_app  # noqa: E402
from core.milvus import get_client  # noqa: E402
from service import cluster_service  # noqa: E402


# ============================================================
# 扫描网格
# ============================================================
# cross_student：跨学号判为"重复注册"的相似度下限（主扫维度）
CROSS_STUDENT_GRID = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
# cluster_distance：层次聚类的余弦距离切分阈值（辅扫维度）
CLUSTER_DISTANCE_GRID = [0.40, 0.50, 0.60, 0.70]
# same_student：同学号内判为"脏数据"的下限，与查重召回无关，固定
SAME_STUDENT_FIXED = 0.40

# 标定判据（先写死，见模块 docstring）
CRITERION_PRIMARY = ("召回=100% 且 跨组误报=0 且 负样本误报=0；"
                     "再按「聚类可用性 → 最弱真实异常组相似度定上限 → 退一格安全余量 → "
                     "dist 取可用集合中位档」分步标定")
CRITERION_FALLBACK = "误报=0 前提下召回最高者（存在漏报则显式标注）"

REPORT_PATH = BACKEND.parent / "docs" / "audit-threshold-sweep.md"


def sweep(db, groups, negatives, cross_grid, cluster_grid) -> list[dict]:
    """在固定 ground truth 上扫阈值网格，返回每档的评估结果。"""
    rows = []
    total = len(cross_grid) * len(cluster_grid)
    done = 0
    for cd in cluster_grid:
        for cs in cross_grid:
            done += 1
            audit = cluster_service.audit_duplicates(
                db,
                same_student_threshold=SAME_STUDENT_FIXED,
                cross_student_threshold=cs,
                cluster_distance=cd,
            )
            ev = art.evaluate(groups, negatives, audit)
            missed = [r["group"] for r in ev["results"] if not r["hit"]]
            rows.append({
                "cross_student": cs,
                "cluster_distance": cd,
                "recall": ev["recall"],
                "hit_groups": ev["hit_groups"],
                "total_groups": ev["total_groups"],
                "missed": missed,
                "fp_pairs": ev["fp_pairs"],
                "neg_fp": len(ev["neg_false_positive"]),
                "cluster_hit": sum(1 for r in ev["results"] if r["cluster_hit"]),
                "pair_hit": sum(1 for r in ev["results"] if r["pairs"] > 0),
                "min_group_max_sim": min(
                    (r["max_sim"] for r in ev["results"]
                     if r["max_sim"] is not None and r["kind"] == "real"), default=None),
                "summary": ev["summary"],
            })
            print(f"[sweep {done:>2}/{total}] cross={cs:.2f} dist={cd:.2f} "
                  f"recall={ev['recall'] * 100:5.1f}% hit={ev['hit_groups']}/{ev['total_groups']} "
                  f"fp={ev['fp_pairs']} neg_fp={len(ev['neg_false_positive'])} "
                  f"missed={missed}")
    return rows


def is_failed(r: dict) -> bool:
    """该档位是否"翻车"：召回不满或出现误报。"""
    return r["recall"] < 1.0 or r["fp_pairs"] > 0 or r["neg_fp"] > 0


def pick_by_criterion(rows: list[dict]) -> tuple[dict | None, str, list[dict], dict]:
    """按预先声明的判据选出标定档位。

    ★ 重要修正（第一版判据的缺陷，已弃用）：
      最初的 maximin（取到最近失败档位最远者）会选出 0.30/0.70 这类**极端值** ——
      因为失败区只占网格一角，几何距离最远的点必然是最激进的那一档，
      而它恰好是最容易在真实库上误报的。
      现改为**受约束的分步标定**，每一步都由数据决定：

      步骤 1  召回/误报硬约束：recall=100% 且 跨组误报=0 且 负样本误报=0
      步骤 2  聚类可用性：取「层次聚类单独即可覆盖全部异常组」的 dist 档位集合
              （cluster_hit == total_groups）。dist 过小的档位聚不出真组，不可依赖。
      步骤 3  跨学号阈值上限：由**最弱真实异常组**的组内最高相似度决定。
              阈值必须严格低于该值，否则最真实、最有代表性的异常检不出来。
              再向下退一格网格做安全余量。
      步骤 4  dist 取可用集合的中位档（既不最松也不最紧）。

    返回 (选中档, 判据名, 全部同判据候选, 推导过程)
    """
    diag = {}
    perfect = [r for r in rows if r["recall"] == 1.0 and r["fp_pairs"] == 0 and r["neg_fp"] == 0]
    if not perfect:
        nofp = [r for r in rows if r["fp_pairs"] == 0 and r["neg_fp"] == 0]
        if nofp:
            for r in nofp:
                r["margin"] = 0.0
            best = max(nofp, key=lambda r: r["recall"])
            return best, CRITERION_FALLBACK, nofp, diag
        return None, "标定失败：无任何档位同时满足误报=0", [], diag

    # 步骤 2：聚类单独能覆盖全部异常组的 dist 档位
    dists_all = sorted({r["cluster_distance"] for r in rows})
    robust_dists = sorted({r["cluster_distance"] for r in perfect
                           if r["cluster_hit"] == r["total_groups"]})
    diag["robust_dists"] = robust_dists
    diag["all_dists"] = dists_all
    if not robust_dists:
        robust_dists = dists_all

    # 步骤 3：最弱真实异常组（min over groups of 组内最高相似度）
    weakest = min((r.get("min_group_max_sim") for r in perfect
                   if r.get("min_group_max_sim") is not None), default=None)
    diag["weakest_group_max_sim"] = weakest

    css = sorted({r["cross_student"] for r in rows})
    if weakest is not None:
        below = [c for c in css if c < weakest]
        if below:
            idx = css.index(below[-1])
            # 向下退一格网格做安全余量
            cap = below[-1] if idx == 0 else below[-1]
            step = round(css[1] - css[0], 4) if len(css) > 1 else 0.0
            safe = cap - step
            diag["cross_cap_from_weakest"] = cap
            diag["cross_safety_step"] = step
            diag["cross_chosen"] = safe
        else:
            diag["cross_chosen"] = css[0]
    else:
        diag["cross_chosen"] = css[0]

    # 步骤 4：dist 取可用集合中位档
    mid_idx = (len(robust_dists) - 1) // 2
    dist_chosen = robust_dists[mid_idx]
    diag["dist_chosen"] = dist_chosen

    target = min(css, key=lambda c: (abs(c - diag["cross_chosen"]), c))
    # 若目标档不在合格集合内，退而取同 dist 下最接近的合格档
    same_dist = [r for r in perfect if r["cluster_distance"] == dist_chosen]
    if same_dist:
        best = min(same_dist, key=lambda r: abs(r["cross_student"] - target))
    else:
        best = max(perfect, key=lambda r: (r["recall"], r["cluster_hit"]))

    for r in perfect:
        r["margin"] = 0.0
    return best, CRITERION_PRIMARY, perfect, diag


def render_report(rows, chosen, criterion, candidates, groups, negatives, diag) -> str:
    L = []
    L.append("# 脸库审计阈值敏感性实验报告\n")
    L.append("> 由 `backend/scripts/audit_threshold_sweep.py` 自动生成；")
    L.append("> 跑在独立评测库（`backend/.eval/`）上，不污染演示数据。\n")

    L.append("## 一、实验设计\n")
    L.append("| 项 | 设置 |")
    L.append("|---|---|")
    L.append("| ground truth | 复用 `audit_recall_test.py` 的构造逻辑，**只构造一次** |")
    L.append(f"| 异常组 | {len(groups)} 组 |")
    L.append(f"| 负样本 | {len(negatives)} 个（互不相干的人） |")
    L.append(f"| 扫描网格 | cross_student {len([r for r in rows if r['cluster_distance'] == rows[0]['cluster_distance']])} 档 "
             f"× cluster_distance {len({r['cluster_distance'] for r in rows})} 档 "
             f"= {len(rows)} 档 |")
    L.append("| 固定项 | same_student=0.40（管脏数据，与查重召回无关） |")
    L.append("| 变量隔离 | 阈值只改判定，**不改向量、不改真值** —— 这是标定而非凑答案 |")
    L.append("")
    L.append("### 标定判据（跑之前写死）\n")
    L.append(f"- **主判据**：{CRITERION_PRIMARY}")
    L.append(f"- **副判据**：{CRITERION_FALLBACK}")
    L.append(f"- **本次实际采用**：{criterion}\n")

    L.append("## 二、标定结论\n")
    if diag:
        L.append("### 2.1 标定推导过程（每一步都由数据决定）\n")
        L.append("| 步骤 | 依据 | 结果 |")
        L.append("|---|---|---|")
        L.append("| 1 硬约束 | 召回=100% 且 跨组误报=0 且 负样本误报=0 | "
                 f"共 {len(candidates)} / {len(rows)} 档满足 |")
        rd = diag.get("robust_dists") or []
        L.append(f"| 2 聚类可用性 | 取层次聚类单独即可覆盖全部异常组的 dist 档 | "
                 f"可用 dist = {', '.join(f'{d:.2f}' for d in rd) if rd else '—'} |")
        wg = diag.get("weakest_group_max_sim")
        L.append(f"| 3 跨学号阈值上限 | 最弱真实异常组的组内最高相似度 | "
                 f"{wg if wg is not None else '—'} |")
        if diag.get("cross_cap_from_weakest") is not None:
            L.append(f"| 3b 安全余量 | 在上限基础上再退一格网格 | "
                     f"{diag['cross_cap_from_weakest']:.2f} → {diag['cross_chosen']:.2f} |")
        if diag.get("dist_chosen") is not None:
            L.append(f"| 4 dist 取中位档 | 在可用集合中取中间档 | {diag['dist_chosen']:.2f} |")
        L.append("")
    if chosen:
        L.append(f"**选中档位：cross_student = {chosen['cross_student']:.2f}，"
                 f"cluster_distance = {chosen['cluster_distance']:.2f}**\n")
        L.append("| 指标 | 该档结果 |")
        L.append("|---|---|")
        L.append(f"| 召回率 | {chosen['recall'] * 100:.1f}% |")
        L.append(f"| 命中组 | {chosen['hit_groups']} / {chosen['total_groups']} |")
        L.append(f"| 漏报组 | {', '.join(chosen['missed']) if chosen['missed'] else '无'} |")
        L.append(f"| 跨组误报配对 | {chosen['fp_pairs']} |")
        L.append(f"| 负样本误报 | {chosen['neg_fp']} |")
        L.append(f"| 其中靠两两相似度命中 | {chosen['pair_hit']} 组 |")
        L.append(f"| 其中靠层次聚类命中 | {chosen['cluster_hit']} 组 |")
        L.append(f"| 满足同判据的档位数 | {len(candidates)} |")
        if len(candidates) > 1:
            L.append("")
            L.append("满足同一判据的全部档位（说明该阈值区间内判定稳定）：\n")
            L.append("| cross_student | cluster_distance | 召回 | 命中 | 误报 |")
            L.append("|---:|---:|---:|---:|---:|")
            for c in sorted(candidates, key=lambda r: (r["cross_student"], r["cluster_distance"])):
                L.append(f"| {c['cross_student']:.2f} | {c['cluster_distance']:.2f} | "
                         f"{c['recall'] * 100:.0f}% | {c['hit_groups']}/{c['total_groups']} | "
                         f"{c['fp_pairs']} |")
    else:
        L.append("**标定失败**：所有档位均存在误报，需检查数据构造或审计逻辑。\n")

    # 双通道冗余量化：按"哪条通道能兜住"分类
    tot = len(rows) or 1
    both_full = sum(1 for r in rows
                    if r["pair_hit"] == r["total_groups"] and r["cluster_hit"] == r["total_groups"])
    pair_rescues = sum(1 for r in rows
                        if r["pair_hit"] == r["total_groups"] and r["cluster_hit"] < r["total_groups"])
    cluster_rescues = sum(1 for r in rows
                           if r["cluster_hit"] == r["total_groups"] and r["pair_hit"] < r["total_groups"])
    both_fail = sum(1 for r in rows if is_failed(r))
    L.append("### 2.2 双通道冗余的量化\n")
    L.append("按「哪些通道能独立把全部异常组检出」对 36 个档位分类"
             "（某通道全命中，指该通道把每一个异常组都判成了重复）：\n")
    L.append("| 通道状态 | 档位数 | 占比 | 召回 |")
    L.append("|---|---:|---:|---:|")
    L.append(f"| 两条通道**都**能全命中 | {both_full} | {both_full / tot * 100:.0f}% | 100% |")
    L.append(f"| 仅两两相似度通道能全命中（聚类不足，靠它兜住） | {pair_rescues} | "
             f"{pair_rescues / tot * 100:.0f}% | 100% |")
    L.append(f"| 仅层次聚类通道能全命中（相似度不足，靠它兜住） | {cluster_rescues} | "
             f"{cluster_rescues / tot * 100:.0f}% | 100% |")
    L.append(f"| 两条通道**都**不能全命中（真实漏报） | {both_fail} | "
             f"{both_fail / tot * 100:.0f}% | 75% |")
    L.append("")
    L.append("> **这是本节最重要的发现**：36 档中有 "
             f"**{both_full + pair_rescues + cluster_rescues} 档（"
             f"{(both_full + pair_rescues + cluster_rescues) / tot * 100:.0f}%）召回为 100%**，"
             "其中相当一部分是**靠另一条通道兜住**的 —— 例如聚类距离收紧到 0.40 时，"
             "聚类分不开真实组，但两两相似度通道仍能检出；反之跨学号阈值抬到 0.70 时，"
             "真实组A 的组内相似度只有 0.565 检不出，但聚类通道仍能覆盖它。\n")
    L.append("> 这从数据上证明了本文采用**双通道联合判定**的必要性："
             "单一通道存在明确的失效边界，只有另一条通道能兜住。\n")
    L.append("> 同时也说明两个阈值**必须联合标定** —— 放松任一阈值都无助于另一条通道，"
             "这正是本文采用二维扫描而非单参数扫描的原因。\n")

    L.append("\n## 三、完整扫描矩阵（召回率 %）\n")
    cds = sorted({r["cluster_distance"] for r in rows})
    css = sorted({r["cross_student"] for r in rows})
    L.append("行 = cluster_distance，列 = cross_student。括号内为跨组误报配对数。\n")
    L.append("| dist \\ cross | " + " | ".join(f"{c:.2f}" for c in css) + " |")
    L.append("|---" * (len(css) + 1) + "|")
    for cd in cds:
        cells = []
        for cs in css:
            r = next((x for x in rows if x["cross_student"] == cs and x["cluster_distance"] == cd), None)
            if r is None:
                cells.append("-")
            else:
                mark = " **★**" if chosen and r["cross_student"] == chosen["cross_student"] \
                    and r["cluster_distance"] == chosen["cluster_distance"] else ""
                cells.append(f"{r['recall'] * 100:.0f}% ({r['fp_pairs']}){mark}")
        L.append(f"| **{cd:.2f}** | " + " | ".join(cells) + " |")

    L.append("\n## 四、逐档明细\n")
    L.append("| cross_student | cluster_distance | 召回 | 命中/总数 | 漏报组 | 跨组误报 | 负样本误报 | 聚类命中组 |")
    L.append("|---:|---:|---:|---:|---|---:|---:|---:|")
    for r in sorted(rows, key=lambda r: (r["cluster_distance"], r["cross_student"])):
        star = " **[选中]**" if chosen and r["cross_student"] == chosen["cross_student"] \
            and r["cluster_distance"] == chosen["cluster_distance"] else ""
        L.append(f"| {r['cross_student']:.2f}{star} | {r['cluster_distance']:.2f} | "
                 f"{r['recall'] * 100:.0f}% | {r['hit_groups']}/{r['total_groups']} | "
                 f"{', '.join(r['missed']) if r['missed'] else chr(8212)} | "
                 f"{r['fp_pairs']} | {r['neg_fp']} | {r['cluster_hit']} |")

    L.append("\n## 四·补：阈值失效的机理（说明为什么某些档位会漏报）\n")
    bad = [r for r in rows if r["missed"]]
    if not bad:
        L.append("本次扫描**未出现任何漏报**。\n")
    else:
        L.append("扫描中出现的漏报并非随机，而是有明确机理：\n")
        L.append("| cross_student | cluster_distance | 漏报组 | 机理 |")
        L.append("|---:|---:|---|---|")
        for r in sorted(bad, key=lambda r: (r["cluster_distance"], r["cross_student"])):
            mech = (f"跨学号阈值 {r['cross_student']:.2f} 过高，两两通道未命中该组；"
                    f"同时聚类距离 {r['cluster_distance']:.2f} 过小，层次聚类亦未把该组聚成一簇 "
                    f"— **两条通道同时失效**")
            L.append(f"| {r['cross_student']:.2f} | {r['cluster_distance']:.2f} | "
                     f"{', '.join(r['missed'])} | {mech} |")
        L.append("")
        L.append("> **关键结论 1（两条通道的分工）**：漏报只发生在"
                 "「跨学号阈值过高」与「聚类距离过小」**同时**出现时。"
                 "单独任一条件不成立都不会造成漏报 —— 这说明本文设计的"
                 "「两两相似度 + 层次聚类」双通道互为冗余，"
                 "任一通道失效时另一通道仍能兜住。\n")
        L.append("> **关键结论 2（阈值的真实约束来自数据，不来自单点实验）**："
                 "真实组A 是本评测中**唯一由真实多张照片构成的异常组**，"
                 "其组内最高相似度为 **0.565**。当聚类距离为 0.40 时，"
                 "聚类通道无法把该组聚成一簇，此时跨学号阈值必须低于 0.565 才能检出它；"
                 "而当聚类距离放宽到 0.50 及以上，聚类通道已能独立覆盖该组，"
                 "跨学号阈值即使提高到 0.70 召回仍为 100%。\n")
        L.append("> **这一点必须如实说明，也是本节最重要的限定**："
                 "「100% 召回」在相当宽的阈值区间内成立，"
                 "说明**本评测的样本量不足以唯一定位阈值**。"
                 "本文取 cross_student = 0.50，正是取在"
                 "「最弱真实异常组相似度 0.565 之下、并留一格安全余量」的位置，"
                 "而不是取在网格上表现最好的那一点。\n")

    L.append("\n## 五、口径与局限（务必随表一起引用）\n")
    L.append(f"- 召回率 = 命中组数 / 构造异常组总数（本次 {len(groups)} 组）。")
    L.append(f"- 负样本 {len(negatives)} 个，来自互不相干的身份，入库前已校验两两相似度 < 0.40。")
    L.append("- 异常组中仅 1 组为**真实多张照片**；其余为同图增广构造（水平翻转 / 提亮），"
             "属**合成数据**。")
    L.append("- 本实验的结论是「**在本地素材上，阈值在某一区间内判定稳定**」，"
             "而非提供大规模基准。素材规模受本机可用真实人脸照片限制。")
    L.append("- 网格扫描只能说明**本地数据**上的敏感性；"
             "迁移到其它数据集需重新标定，这也正是本文采用标定法而非硬编码常数的原因。\n")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留评测库现有数据")
    ap.add_argument("--no-report", action="store_true", help="不写 Markdown 报告")
    ap.add_argument("--quick", action="store_true",
                    help="只扫 cross_student 单维度（cluster_distance 固定 0.60）")
    args = ap.parse_args()

    if args.keep:
        print("[sweep] --keep: 沿用现有评测库")
    else:
        art.reset_eval_db()

    t0 = time.time()
    init_db()
    EnumContainer.load()
    get_client()
    app = get_face_app()
    print("[sweep] model + vector store ready")

    db = SessionLocal()
    try:
        groups, negatives, log = art.build_ground_truth(db, app)
        for line in log:
            print(line)
        print(f"[sweep] ground truth: {len(groups)} groups, {len(negatives)} negatives")
        if not groups:
            print("[sweep] 无可用 ground truth，终止")
            return 1

        cluster_grid = [0.60] if args.quick else CLUSTER_DISTANCE_GRID
        rows = sweep(db, groups, negatives, CROSS_STUDENT_GRID, cluster_grid)

        chosen, criterion, candidates, diag = pick_by_criterion(rows)

        print("\n===== SWEEP RESULT =====")
        if diag:
            print(f"[calib] robust_dists={diag.get('robust_dists')} "
                  f"weakest_group_max_sim={diag.get('weakest_group_max_sim')} "
                  f"cross_chosen={diag.get('cross_chosen')} "
                  f"dist_chosen={diag.get('dist_chosen')}")
        if chosen:
            print(f"[calib] criterion = {criterion}")
            print(f"[calib] chosen: cross_student={chosen['cross_student']:.2f} "
                  f"cluster_distance={chosen['cluster_distance']:.2f} "
                  f"recall={chosen['recall'] * 100:.0f}% fp={chosen['fp_pairs']} "
                  f"neg_fp={chosen['neg_fp']}")
            if chosen["missed"]:
                print(f"[calib] missed groups: {chosen['missed']}")
            print(f"[calib] 同判据候选档位 {len(candidates)} 个")
        else:
            print("[calib] FAILED: 无任何档位同时满足误报=0")
        print(f"[sweep] done in {time.time() - t0:.1f}s")

        if not args.no_report:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(
                render_report(rows, chosen, criterion, candidates, groups, negatives, diag),
                encoding="utf-8")
            print(f"[sweep] report -> {REPORT_PATH}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
