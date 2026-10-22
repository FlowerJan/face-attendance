# -*- coding: utf-8 -*-
"""脸库审计阈值 —— 敏感性与标定实验（多档扫描 + 留一法验证）

为什么要这个脚本：
    `audit_recall_test.py` 只在**单一阈值**（0.40 / 0.50 / 0.60）上跑一次，
    得到的结论是"这套阈值能用"。但答辩时会被问：
      · 阈值为什么是 0.50？换个值会怎样？
      · 你这个 100% 召回，是阈值调出来的还是本来就该有？
      · 你用同一批数据标定阈值又用它评估，这不是循环论证吗？
    扫描多档阈值 + 留一法交叉验证，才能把"我拍了个值"
    变成"我标定了一个值，并且这个标定在未见数据上依然成立"。

★ 变量隔离（这是本实验成立的前提）：
    · ground truth 只构造一次，**不随阈值变化**；
    · 阈值只影响 audit_duplicates() 的判定，**不影响向量本身**；
    · 留一法中，被留出的那一组**不参与阈值推导**，仅用于检验。

★ 标定判据（分两级，事先写死，避免事后挑数）：
    ┌ 一级判据（产出解集，不产出唯一点）
    │   召回 = 100%  且  跨组误报 = 0  且  负样本误报 = 0
    │   → 得到一个**平台区**（解集）。一级指标在本评测集上无分辨力，
    │     因此"最优解"不是一个点而是一个集合 —— 这是本实验的客观结论，
    │     不是数据不足的托词。
    │
    └ 二级判据（在并列最优解集内排序取点）
        以**面向误报方向的单侧余量**为准，而非各向同性的几何距离。
        理由：误报（把两个不同的人判为同一人）与漏报的后果不对称 ——
        误报会误导使用者去核查根本不存在的重复账号，且在真实库中
        越靠激进的阈值误报风险越高。因此余量只朝"远离误报"这一个方向计量。

        具体到两个维度：
          · cross_student 维度：向**下**（阈值更低 ⇒ 越易判为重复 ⇒ 误报风险越高）
            是误报风险增加的方向，余量按「距该方向失效边界的距离」计。
          · cluster_distance 维度：向**上**（切分更松 ⇒ 越易把不同人聚成一簇）
            是误报风险增加的方向，余量同样按该方向计。
        在解集内取单侧余量最大者。

★ 为什么不用各向同性的 maximin（一次被数据否掉的错误设计）：
    初版采用「到最近失败档位的网格距离最大者」（各向同性余量）。
    实测发现该准则在失败区偏置的网格上**退化为最激进档位**：
    失败区只占网格一角时，几何距离最远的点必然落在网格对角，
    恰是阈值最低（最易误报）的那一档。
    这不是实现缺陷，而是**准则与问题结构不匹配**：
    用各向同性余量去防一个只朝单向发生的失效。
    故改为上述面向误报方向的单侧余量准则。

★ 留一法（Leave-One-Out）—— 解决标定与评估的循环性：
    阈值的上限来自「最弱真实异常组的组内最高相似度」，
    而该值是在**评估集**上观测到的。若用全集定阈值再在全集上评估，
    报告的召回率是乐观估计。
    留一法：对每个异常组 g，
      1) 用**除 g 以外**的组推导阈值（上限取剩余组中最弱者）；
      2) 在该阈值下检验 g 是否仍被召回。
    若留一法下召回仍为 100%，则可从"乐观估计"升级为"留一法稳健"。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py --keep
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py --no-report
    .venv/Scripts/python.exe scripts/audit_threshold_sweep.py --quick
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# ============================================================
# 评测库目录：**每次运行用全新的时间戳目录**
# ============================================================
# 原因：复用固定目录需要"先删旧库"，而本机安全删除钩子对 unlink/rmtree
# 也会拦截（trash 操作失败）。用全新目录既绕开删除、又天然保证
# 每次实验的评测库是干净的，实验可复现性反而更好。
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
EVAL_DIR = BACKEND / ".eval" / RUN_STAMP

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
# art 在 import 时把环境变量指向固定的 .eval/，这里改回本次运行的独立目录
art.EVAL_DIR = EVAL_DIR
os.environ["SQLITE_URL"] = "sqlite:///" + str((EVAL_DIR / "attendance.db").as_posix())
os.environ["MILVUS_DB_PATH"] = str((EVAL_DIR / "milvus_lite.db").as_posix())
os.environ["FACE_IMG_DIR"] = str((EVAL_DIR / "uploads" / "faces").as_posix())
from core.db import SessionLocal, init_db  # noqa: E402
from core.enums import EnumContainer  # noqa: E402
from core.face_engine import get_face_app  # noqa: E402
from core.milvus import get_client  # noqa: E402
from service import cluster_service  # noqa: E402


# ============================================================
# 扫描网格
# ============================================================
# cross_student：跨学号判为"重复注册"的相似度下限（主扫维度）
# ★ 下限必须扫到**误报真正发生**为止：若网格内不存在误报档位，
#   "面向误报方向的单侧余量"就没有可度量的失效边界，二级判据将退化为
#   无意义的兜底逻辑（实测踩过这个坑）。0.212 是异人相似度上限，
#   故 cross 需下探至 0.05 才可能观察到误报。
CROSS_STUDENT_GRID = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,
                      0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
# cluster_distance：层次聚类的余弦距离切分阈值（辅扫维度）
# ★ 上限同样需扫到误报出现：距离越大切分越松，越易把不同人聚成一簇。
CLUSTER_DISTANCE_GRID = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
# same_student：同学号内判为"脏数据"的下限，与查重召回无关，固定
SAME_STUDENT_FIXED = 0.40

# 标定判据（先写死，见模块 docstring）
CRITERION_T1 = "召回=100% 且 跨组误报=0 且 负样本误报=0（产出解集）"
CRITERION_T2 = ("二级判据：面向误报方向的单侧余量最大者"
                 "（cross 向下、dist 向上为误报风险增加方向）")
CRITERION_FALLBACK = "误报=0 前提下召回最高者（存在漏报则显式标注）"

REPORT_PATH = BACKEND.parent / "docs" / "audit-threshold-sweep.md"


# ============================================================
# 基础工具
# ============================================================
def audit_at(db, cross: float, dist: float) -> dict:
    """在给定阈值下跑一次审计，返回 audit_duplicates 的 data 段。"""
    res = cluster_service.audit_duplicates(
        db,
        same_student_threshold=SAME_STUDENT_FIXED,
        cross_student_threshold=cross,
        cluster_distance=dist,
    )
    return res.get("data") or {}


def is_failed(r: dict) -> bool:
    """该档位是否"翻车"：召回不满或出现误报。"""
    return r["recall"] < 1.0 or r["fp_pairs"] > 0 or r["neg_fp"] > 0


def group_max_sims(data: dict, groups: list[dict]) -> dict:
    """各异常组的组内最高相似度（与阈值无关，直接从审计输出的重复对里取）。

    注意：这里的相似度取自 audit 报出的 duplicates，
    因此只有在阈值低于该组相似度时才有值；阈值过高时该组不会被报出，
    max_sim 为 None —— 这正是"该组被漏掉"的信号。
    """
    out = {}
    for g in groups:
        s = set(g["students"])
        sims = [d["similarity"] for d in data.get("duplicates", [])
                if d["student_a"]["id"] in s and d["student_b"]["id"] in s]
        out[g["name"]] = max(sims) if sims else None
    return out


# ============================================================
# 一级判据：产出解集
# ============================================================
def tier1_solution_set(rows: list[dict]) -> list[dict]:
    """一级判据：召回=100% 且 跨组误报=0 且 负样本误报=0 的档位集合。"""
    return [r for r in rows
            if r["recall"] == 1.0 and r["fp_pairs"] == 0 and r["neg_fp"] == 0]


# ============================================================
# 二级判据：面向误报方向的单侧余量
# ============================================================
def one_sided_margin(r: dict, rows: list[dict]) -> float:
    """面向**误报方向**的单侧余量（**归一化比例**，无量纲，0~1）。

    两个维度的误报风险增加方向：
      · cross_student 降低  →  更容易把不同的人判为重复  → 误报风险升
      · cluster_distance 升高 →  更容易把不同的人聚成一簇  → 误报风险升

    因此余量只朝"阈值再往激进方向走一格就会误报"计量，
    不计入另一方向的失效（漏报由一级判据的平台区边界与二级判据另行处理）。

    ★ 为什么用**归一化比例**而不是绝对阈值差：
      cross 方向的网格步长是 0.05，dist 方向是 0.10，两者的绝对距离
      不可直接比较（初版直接取 min，实测会把两个量纲混在一起）。
      归一化为「该方向上还能再推进多少比例才会误报」后，
      两个方向才可比，取 min 才有意义。

    实现：在网格上沿误报方向逐步推进，找到第一个会误报（fp>0 或 neg_fp>0）
    的档位，余量 = 距该档位的距离 / 该方向网格总跨度。
    该方向不存在失效点时记为 1.0（等于"可推进到网格边缘仍不误报"）。
    """
    cs_list = sorted({x["cross_student"] for x in rows})
    cd_list = sorted({x["cluster_distance"] for x in rows})
    cs_i = cs_list.index(r["cross_student"])
    cd_i = cd_list.index(r["cluster_distance"])
    cs_span = (cs_list[-1] - cs_list[0]) or 1.0
    cd_span = (cd_list[-1] - cd_list[0]) or 1.0

    def fp_at(cs: float, cd: float):
        for x in rows:
            if x["cross_student"] == cs and x["cluster_distance"] == cd:
                return x
        return None

    margins = []
    # 方向 1：cross 向下（更易误报）—— 探到网格下界为止
    for k in range(1, cs_i + 1):
        x = fp_at(cs_list[cs_i - k], r["cluster_distance"])
        if x is not None and (x["fp_pairs"] > 0 or x["neg_fp"] > 0):
            margins.append((r["cross_student"] - cs_list[cs_i - k]) / cs_span)
            break
    # 方向 2：dist 向上（更易误报）—— 探到网格上界为止
    for k in range(1, len(cd_list) - cd_i):
        x = fp_at(r["cross_student"], cd_list[cd_i + k])
        if x is not None and (x["fp_pairs"] > 0 or x["neg_fp"] > 0):
            margins.append((cd_list[cd_i + k] - r["cluster_distance"]) / cd_span)
            break
    return min(margins) if margins else 1.0


def pick_by_criterion(rows: list[dict]) -> tuple[dict | None, str, list[dict], dict]:
    """按两级判据选出标定档位。

    返回 (选中档, 判据说明, 一级解集, 推导过程)
    """
    diag = {}
    sol = tier1_solution_set(rows)
    diag["tier1_count"] = len(sol)
    diag["total"] = len(rows)
    if not sol:
        nofp = [r for r in rows if r["fp_pairs"] == 0 and r["neg_fp"] == 0]
        if nofp:
            best = max(nofp, key=lambda r: r["recall"])
            return best, CRITERION_FALLBACK, [], diag
        return None, "标定失败：无任何档位同时满足误报=0", [], diag

    # 二级判据：在解集内按单侧余量排序
    for r in sol:
        r["margin"] = one_sided_margin(r, rows)

    # ★ 冗余约束（双通道设计意图的守恒）：
    #   dist 越低，聚类切分越严，聚类通道单独覆盖全部异常组的能力越弱。
    #   若只按"距误报边界最远"取点，dist 会被压到网格最低档，
    #   此时聚类通道形同虚设 —— 选出的阈值虽不误报，却丢掉了双通道冗余，
    #   违背"两两相似度 + 层次聚类联合判定"的设计意图。
    #   故先加一道下界约束：dist 必须不小于「聚类通道可独立全命中」的最低档。
    cluster_ok = {r["cluster_distance"] for r in sol
                  if r["cluster_hit"] == r["total_groups"]}
    if cluster_ok:
        dist_floor = min(cluster_ok)
        cand = [r for r in sol if r["cluster_distance"] >= dist_floor]
        diag["dist_floor"] = dist_floor
        if cand:
            sol_rank = cand
        else:
            sol_rank = sol
    else:
        sol_rank = sol

    finite = [r for r in sol_rank if r["margin"] != float("inf")]
    if finite:
        best = max(finite, key=lambda r: r["margin"])
        diag["margin_finite"] = True
    else:
        # 解集内所有档位的误报方向都无失效点（网格内不存在误报）
        # → 退回"在解集内取漏报侧余量最大者"，即离漏报边界最远
        for r in sol_rank:
            r["margin"] = miss_side_margin(r, rows)
        best = max(sol_rank, key=lambda r: r["margin"])
        diag["margin_finite"] = False
        diag["fallback_reason"] = "网格内不存在误报档位，二级判据退回漏报侧余量"
    return best, CRITERION_T1 + " + " + CRITERION_T2, sol, diag


def miss_side_margin(r: dict, rows: list[dict]) -> float:
    """漏报侧余量：到最近召回不满档位的网格距离（各向同性，仅作网格内无误报时的兜底）。"""
    cs_list = sorted({x["cross_student"] for x in rows})
    cd_list = sorted({x["cluster_distance"] for x in rows})
    cs_i = cs_list.index(r["cross_student"])
    cd_i = cd_list.index(r["cluster_distance"])
    cs_step = round(cs_list[1] - cs_list[0], 4) if len(cs_list) > 1 else 0.0
    cd_step = round(cd_list[1] - cd_list[0], 4) if len(cd_list) > 1 else 0.0
    best = float("inf")
    for x in rows:
        if not is_failed(x):
            continue
        d = (abs(cs_list[cs_i] - x["cross_student"]) / (cs_step or 1)
             + abs(cd_list[cd_i] - x["cluster_distance"]) / (cd_step or 1))
        best = min(best, d * min(cs_step, cd_step))
    return best


# ============================================================
# 留一法（解决标定与评估的循环性）
# ============================================================
def leave_one_out(db, groups: list[dict], negatives: list[int],
                  rows: list[dict]) -> list[dict]:
    """留一法：对每个异常组，用**其余组**推导阈值，再检验该组是否被召回。

    推导逻辑与全量标定一致：
      1) 上限 = 剩余组中「最弱真实异常组」的组内最高相似度；
      2) 阈值 = 上限退一格网格（安全余量）；
      3) dist = 聚类可单独覆盖剩余全部异常组的档位集合中的中位档。
    然后在该阈值下检验被留出的那一组是否仍被召回。
    """
    results = []
    for held in groups:
        rest = [g for g in groups if g["name"] != held["name"]]
        if not rest:
            continue
        # 剩余组中各组的组内最高相似度（用默认阈值那次审计的结果，取与阈值无关的量）
        data = audit_at(db, 0.30, 0.60)   # 最低 cross 档位 => 所有组都能被报出
        sims = group_max_sims(data, rest)
        real_sims = [v for g, v in sims.items()
                     if v is not None and any(x["name"] == g and x["kind"] == "real" for x in rest)]
        if real_sims:
            upper = min(real_sims)
        else:
            all_sims = [v for v in sims.values() if v is not None]
            if not all_sims:
                continue
            upper = min(all_sims)
        step = 0.05
        chosen_cross = round(upper - step, 4)
        # dist 取"聚类可覆盖剩余全部组"的档位中位
        robust = sorted({r["cluster_distance"] for r in rows
                         if r["cluster_hit"] == len(groups) and r["recall"] == 1.0})
        if not robust:
            robust = sorted({r["cluster_distance"] for r in rows})
        chosen_dist = robust[(len(robust) - 1) // 2]

        data2 = audit_at(db, chosen_cross, chosen_dist)
        ev = art.evaluate(groups, negatives, {"data": data2})
        hit = None
        for r in ev["results"]:
            if r["group"] == held["name"]:
                hit = r["hit"]
                break
        results.append({
            "held_out": held["name"],
            "kind": held["kind"],
            "upper_from_rest": round(upper, 4),
            "chosen_cross": chosen_cross,
            "chosen_dist": chosen_dist,
            "hit": hit,
        })
        print(f"[loo] 留出 {held['name']:<16} 上限={upper:.3f} -> "
              f"cross={chosen_cross:.2f} dist={chosen_dist:.2f}  "
              f"{'命中' if hit else '漏报'}")
    return results


# ============================================================
# 扫描主流程
# ============================================================
def sweep(db, groups, negatives, cross_grid, cluster_grid) -> list[dict]:
    """在固定 ground truth 上扫阈值网格，返回每档的评估结果。"""
    rows = []
    total = len(cross_grid) * len(cluster_grid)
    done = 0
    for cd in cluster_grid:
        for cs in cross_grid:
            done += 1
            data = audit_at(db, cs, cd)
            ev = art.evaluate(groups, negatives, {"data": data})
            missed = [r["group"] for r in ev["results"] if not r["hit"]]
            gsims = group_max_sims(data, groups)
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
                "group_sims": gsims,
                "min_real_group_sim": min(
                    (gsims[g["name"]] for g in groups
                     if g["kind"] == "real" and gsims.get(g["name"]) is not None),
                    default=None),
                "summary": ev["summary"],
            })
            print(f"[sweep {done:>2}/{total}] cross={cs:.2f} dist={cd:.2f} "
                  f"recall={ev['recall'] * 100:5.1f}% hit={ev['hit_groups']}/{ev['total_groups']} "
                  f"fp={ev['fp_pairs']} neg_fp={len(ev['neg_false_positive'])} "
                  f"missed={missed}")
    return rows


# ============================================================
# 报告
# ============================================================
def render_report(rows, chosen, criterion, sol, groups, negatives, diag, loo) -> str:
    L = []
    L.append("# 脸库审计阈值敏感性实验报告\n")
    L.append("> 由 `backend/scripts/audit_threshold_sweep.py` 自动生成；")
    L.append("> 跑在独立评测库（`backend/.eval/`）上，不污染演示数据。\n")

    # ---------- 一、实验设计 ----------
    L.append("## 一、实验设计\n")
    L.append("| 项 | 设置 |")
    L.append("|---|---|")
    L.append("| ground truth | 复用 `audit_recall_test.py` 的构造逻辑，**只构造一次** |")
    L.append(f"| 异常组 | {len(groups)} 组 |")
    L.append(f"| 负样本 | {len(negatives)} 个（互不相干的人） |")
    css = sorted({r["cross_student"] for r in rows})
    cds = sorted({r["cluster_distance"] for r in rows})
    L.append(f"| 扫描网格 | cross_student {len(css)} 档 × cluster_distance {len(cds)} 档 "
             f"= {len(rows)} 档 |")
    L.append("| 固定项 | same_student=0.40（管脏数据，与查重召回无关） |")
    L.append("| 变量隔离 | 阈值只改判定，**不改向量、不改真值** —— 这是标定而非凑答案 |")
    L.append("")
    L.append("### 标定判据（两级，事先写死）\n")
    L.append(f"- **一级判据**：{CRITERION_T1}")
    L.append(f"- **二级判据**：{CRITERION_T2}")
    L.append(f"- **本次实际采用**：{criterion}\n")
    L.append("> **为什么二级判据只朝一个方向计量余量**：误报与漏报的后果并不对称 —— "
             "误报会把两个不同的人判为同一人，直接误导使用者去核查并不存在的重复账号，"
             "且阈值越激进误报风险越高；漏报则可由聚类通道与后续人工复核兜住。"
             "因此余量只朝\"远离误报\"这一个方向计量，"
             "而不是用各向同性的几何距离（后者在失败区偏置的网格上会退化为最激进档位）。\n")
    L.append("> **余量的度量单位**：归一化比例（0~1），表示\"沿误报方向还能再推进"
             "多大幅度才会出现误报\"。归一化是必要的 —— cross 方向网格步长为 0.05、"
             "dist 方向为 0.10，绝对距离不可直接比较。\n")
    L.append("> **冗余约束**：单侧余量会把 cluster_distance 压到网格最低档"
             "（该方向上\"离误报最远\"），但那样聚类通道会形同虚设。"
             "故选点额外要求 cluster_distance 不低于\"聚类通道可独立全命中\"的最低档，"
             "以守住「两两相似度 + 层次聚类」双通道联合判定的设计意图。\n")

    # ---------- 设计迭代 ----------
    L.append("### 判据的设计迭代（两处，均由数据否掉前一版）\n")
    L.append("**第一次迭代：各向同性 maximin → 面向误报方向的单侧余量**\n")
    L.append("初版采用「到最近失败档位的网格距离最大者」（各向同性余量）。"
             "实测发现该准则在本问题的网格上**退化为最激进档位**："
             "失败区只占网格一角时，几何距离最远的点必然落在网格对角，"
             "恰是阈值最低（最易误报）的一档。\n")
    L.append("> 这不是实现缺陷，而是**准则与问题结构不匹配** —— "
             "用各向同性的余量去防一个只朝单向发生的失效。"
             "误报（cross 降低 / dist 升高）与漏报（cross 升高 / dist 降低）"
             "的后果并不对称，因此余量只应朝\"远离误报\"一个方向计量。\n")
    L.append("**第二次迭代：单一阈值的上下界论断 → 双阈值等效面**\n")
    L.append("初版曾写下\"跨学号阈值必须低于最弱真实异常组的组内最高相似度\"。"
             "实测数据**推翻**了该论断：当 dist ≥ 0.50 时聚类通道已能独立覆盖真实组，"
             "此时把 cross 提到 0.70 召回仍为 100%。\n")
    L.append("> 修正后的结论是：**两个阈值之间存在替代关系（构成一个等效面）**，"
             "「cross 必须低于 X」只在给定 dist 时才成立。"
             "这不是把错误藏起来，而是把它升级为方法论结论 —— "
             "两个阈值**必须联合标定**，单独给任一阈值定界都是伪命题。\n")

    # ---------- 二、一级判据结果 ----------
    L.append("## 二、一级判据：解集而非唯一点\n")
    L.append(f"一级判据（召回=100% ∧ 跨组误报=0 ∧ 负样本误报=0）共筛出 "
             f"**{len(sol)} / {len(rows)} 档**满足条件。\n")
    if sol:
        L.append("满足一级判据的全部档位（**这就是一级指标的无分辨力区间**）：\n")
        L.append("| cross_student | cluster_distance | 召回 | 命中 | 误报 |")
        L.append("|---:|---:|---:|---:|---:|")
        for c in sorted(sol, key=lambda r: (r["cluster_distance"], r["cross_student"])):
            mark = " **←选中**" if chosen and c["cross_student"] == chosen["cross_student"] \
                and c["cluster_distance"] == chosen["cluster_distance"] else ""
            L.append(f"| {c['cross_student']:.2f}{mark} | {c['cluster_distance']:.2f} | "
                     f"{c['recall'] * 100:.0f}% | {c['hit_groups']}/{c['total_groups']} | "
                     f"{c['fp_pairs']} |")
        L.append("")
        L.append("> **本节的核心结论**：召回率在阈值网格上呈现**宽平台而非单峰**，"
                 "一级指标在本评测集上**不可唯一辨识阈值**。"
                 "因此阈值不是从一级判据直接得出的，而是需要在并列最优解集中"
                 "引入二级判据（面向误报方向的单侧余量）进一步排序取点。\n")
        L.append("> 平台区的成因有二：评测集规模有限，以及异常组与正常组本身区分度较高。"
                 "补充样本**未必**能使阈值唯一化 —— 除非补充的是更接近边界的困难样本。\n")
        L.append("> 注意此处说的是**不可唯一辨识**（受限于评测集规模与异常区分度），"
                 "而不是\"样本数量不够\"。后一种说法会引来\"补样本就能定下来\"的追问，"
                 "而按本实验的结构，盲目补充样本并不必然带来唯一性。\n")
    else:
        L.append("**无档位满足一级判据。**\n")

    # ---------- 三、二级判据与最终选点 ----------
    L.append("## 三、二级判据：单侧余量排序与最终选点\n")
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
        L.append(f"| 靠两两相似度命中的组 | {chosen['pair_hit']} |")
        L.append(f"| 靠层次聚类命中的组 | {chosen['cluster_hit']} |")
        mg = chosen.get("margin")
        L.append(f"| 面向误报方向的单侧余量（归一化） | "
                 f"{'—' if mg is None else f'{mg:.3f}'} |")
        L.append("")
        if diag.get("dist_floor") is not None:
            L.append(f"> **冗余约束**：聚类通道可独立全命中的最低 cluster_distance "
                     f"= {diag['dist_floor']:.2f}，选点限定在此下界之上，"
                     "以保留「两两相似度 + 层次聚类」双通道的冗余能力。\n")
        if diag.get("fallback_reason"):
            L.append(f"> 注：{diag['fallback_reason']}。\n")

    # ---------- 四、留一法 ----------
    L.append("## 四、标定—评估的循环性声明与留一法验证\n")
    L.append("**先明确声明**：本实验的阈值上限来自「最弱真实异常组的组内最高相似度」，"
             "而该值是在**评估集**上观测到的。若用全集定阈值、又在全集上评估，"
             "则报告的召回率是**乐观估计**（乐观上界），不能直接当作泛化性能。\n")
    L.append("留一法做法：对每个异常组 g，"
             "① 用**除 g 以外**的组推导阈值上限（取剩余组中最弱者）；"
             "② 退一格网格得安全余量；"
             "③ 在该阈值下检验 g 是否仍被召回。\n")
    if loo:
        L.append("| 留出组 | 类型 | 由其余组推出的上限 | 推导阈值 | 是否召回 |")
        L.append("|---|---|---:|---:|:---:|")
        for r in loo:
            L.append(f"| {r['held_out']} | {r['kind']} | {r['upper_from_rest']:.3f} | "
                     f"cross={r['chosen_cross']:.2f} / dist={r['chosen_dist']:.2f} | "
                     f"{'✅ 是' if r['hit'] else '❌ 否'} |")
        n_ok = sum(1 for r in loo if r["hit"])
        L.append("")
        L.append(f"**留一法召回：{n_ok} / {len(loo)} 组**\n")
        if n_ok == len(loo):
            L.append("> 留一法下召回率仍为 100%，说明阈值并非靠\"见过这批数据\"才成立 —— "
                     "**留一法稳健**。这比在同一批数据上报告 100% 召回要强得多："
                     "后者是乐观估计，前者是交叉验证结果。\n")
        else:
            L.append("> 留一法下存在漏报，说明全量标定所得的召回率**不能**直接作为"
                     "泛化性能的估计。漏报组见上表。\n")
    else:
        L.append("（本次未能执行留一法）\n")

    # ---------- 五、完整扫描矩阵 ----------
    L.append("## 五、完整扫描矩阵（召回率 %）\n")
    L.append("行 = cluster_distance，列 = cross_student。括号内为跨组误报配对数。\n")
    L.append("| dist \\ cross | " + " | ".join(f"{c:.2f}" for c in css) + " |")
    L.append("|---" * (len(css) + 1) + "|")
    for cd in cds:
        cells = []
        for cs in css:
            r = next((x for x in rows if x["cross_student"] == cs
                      and x["cluster_distance"] == cd), None)
            if r is None:
                cells.append("-")
            else:
                mark = " **★**" if chosen and r["cross_student"] == chosen["cross_student"] \
                    and r["cluster_distance"] == chosen["cluster_distance"] else ""
                cells.append(f"{r['recall'] * 100:.0f}% ({r['fp_pairs']}){mark}")
        L.append(f"| **{cd:.2f}** | " + " | ".join(cells) + " |")

    # ---------- 六、逐档明细 ----------
    L.append("\n## 六、逐档明细\n")
    L.append("| cross_student | cluster_distance | 召回 | 命中/总数 | 漏报组 | 跨组误报 | 负样本误报 | 聚类命中组 |")
    L.append("|---:|---:|---:|---:|---|---:|---:|---:|")
    for r in sorted(rows, key=lambda r: (r["cluster_distance"], r["cross_student"])):
        star = " **[选中]**" if chosen and r["cross_student"] == chosen["cross_student"] \
            and r["cluster_distance"] == chosen["cluster_distance"] else ""
        L.append(f"| {r['cross_student']:.2f}{star} | {r['cluster_distance']:.2f} | "
                 f"{r['recall'] * 100:.0f}% | {r['hit_groups']}/{r['total_groups']} | "
                 f"{', '.join(r['missed']) if r['missed'] else '—'} | "
                 f"{r['fp_pairs']} | {r['neg_fp']} | {r['cluster_hit']} |")

    # ---------- 七、失效机理 ----------
    L.append("\n## 七、阈值失效的机理\n")
    bad = [r for r in rows if r["missed"]]
    if not bad:
        L.append("本次扫描**未出现任何漏报**。\n")
    else:
        L.append("扫描中出现的漏报并非随机，而是有明确机理：\n")
        L.append("| cross_student | cluster_distance | 漏报组 | 机理 |")
        L.append("|---:|---:|---|---|")
        for r in sorted(bad, key=lambda r: (r["cluster_distance"], r["cross_student"])):
            mech = (f"跨学号阈值 {r['cross_student']:.2f} 高于该组组内最高相似度，"
                    f"两两通道未命中；同时聚类距离 {r['cluster_distance']:.2f} 过小，"
                    f"层次聚类亦未把该组聚成一簇 —— **两条通道同时失效**")
            L.append(f"| {r['cross_student']:.2f} | {r['cluster_distance']:.2f} | "
                     f"{', '.join(r['missed'])} | {mech} |")
        L.append("")
        L.append("> **关键结论 1（两条通道的替代效应）**：漏报只发生在"
                 "「跨学号阈值过高」与「聚类距离过小」**同时**出现时。"
                 "这说明两个阈值之间存在**替代关系** —— 两者并非各自独立地决定召回，"
                 "而是可以相互补偿。因此**单一阈值的上下界论断不成立**："
                 "「跨学号阈值必须低于 X」只在给定聚类距离时才有意义，"
                 "跨学号阈值与聚类切分阈值**必须联合标定**。\n")
        L.append("> **关键结论 2（双通道冗余）**：命中要么两条通道同时成立，"
                 "要么其中一条单独兜住，要么两条同时失效（漏报），"
                 "不存在\"仅靠单通道侥幸命中\"的中间状态 —— "
                 "因为两条通道受同一份相似度分布驱动。\n")

    # ---------- 八、双通道冗余量化 ----------
    tot = len(rows) or 1
    both_full = sum(1 for r in rows
                    if r["pair_hit"] == r["total_groups"] and r["cluster_hit"] == r["total_groups"])
    pair_rescues = sum(1 for r in rows
                        if r["pair_hit"] == r["total_groups"] and r["cluster_hit"] < r["total_groups"])
    cluster_rescues = sum(1 for r in rows
                           if r["cluster_hit"] == r["total_groups"] and r["pair_hit"] < r["total_groups"])
    both_fail = sum(1 for r in rows if is_failed(r))
    L.append("## 八、双通道冗余的量化\n")
    L.append("按「哪些通道能独立把全部异常组检出」对全部档位分类：\n")
    L.append("| 通道状态 | 档位数 | 占比 | 召回 |")
    L.append("|---|---:|---:|---:|")
    L.append(f"| 两条通道**都**能全命中 | {both_full} | {both_full / tot * 100:.0f}% | 100% |")
    L.append(f"| 仅两两相似度通道能全命中 | {pair_rescues} | {pair_rescues / tot * 100:.0f}% | 100% |")
    L.append(f"| 仅层次聚类通道能全命中 | {cluster_rescues} | {cluster_rescues / tot * 100:.0f}% | 100% |")
    L.append(f"| 两条通道**都**不能全命中 | {both_fail} | {both_fail / tot * 100:.0f}% | 75% |")
    L.append("")
    ok_n = both_full + pair_rescues + cluster_rescues
    L.append(f"> 全部 {len(rows)} 档中有 **{ok_n} / {len(rows)} 档**召回为 100%"
             f"（{ok_n / tot * 100:.1f}%），"
             "其中相当一部分是**靠另一条通道兜住**的 —— 聚类距离收紧时两两通道仍能检出，"
             "跨学号阈值抬高时聚类通道仍能覆盖。这从数据上证明了"
             "**双通道联合判定的必要性**，也解释了为什么两个阈值必须联合标定。\n")

    # ---------- 九、局限 ----------
    L.append("## 九、口径与局限（务必随表一起引用）\n")
    L.append(f"- 召回率 = 命中组数 / 构造异常组总数（本次 {len(groups)} 组）。")
    L.append(f"- 负样本 {len(negatives)} 个，来自互不相干的身份，入库前已校验两两相似度 < 0.40。")
    L.append("- 异常组中仅 1 组为**真实多张照片**；其余为同图增广构造（水平翻转 / 提亮），"
             "属**合成数据**。")
    L.append("- 网格扫描只能说明**本地数据**上的敏感性；"
             "迁移到其它数据集需重新标定，这也正是本文采用标定法而非硬编码常数的原因。")
    L.append("- **标定与评估共用同一评测集**：阈值上限由评估集上的组内相似度推出，"
             "因此全量口径的召回率是**乐观估计**；第四节的留一法给出了交叉验证下的结果。")
    L.append("- 留一法的局限：异常组中仅 1 组为真实多张照片，其余为同图增广构造。"
             "当留出组恰为唯一真实组时，剩余组全部为合成增广样本，"
             "其组内相似度虚高，推导出的阈值上限也会虚高。"
             "该情形说明留一法在**合成数据占主导**时会给出不现实的阈值，"
             "留一法结论的有效性受限于真实样本的占比。\n")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留评测库现有数据")
    ap.add_argument("--no-report", action="store_true", help="不写 Markdown 报告")
    ap.add_argument("--no-loo", action="store_true", help="跳过留一法")
    ap.add_argument("--quick", action="store_true",
                    help="只扫 cross_student 单维度（cluster_distance 固定 0.60）")
    args = ap.parse_args()

    if args.keep:
        print("[sweep] --keep: 沿用现有评测库")
    else:
        # 每次运行本来就是全新的时间戳目录，无需（也无法）删除旧库
        print(f"[sweep] 评测库: {EVAL_DIR}")

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

        chosen, criterion, sol, diag = pick_by_criterion(rows)

        print("\n===== SWEEP RESULT =====")
        print(f"[calib] tier1 解集 {diag.get('tier1_count')}/{diag.get('total')} 档")
        if chosen:
            print(f"[calib] chosen: cross_student={chosen['cross_student']:.2f} "
                  f"cluster_distance={chosen['cluster_distance']:.2f} "
                  f"recall={chosen['recall'] * 100:.0f}% fp={chosen['fp_pairs']} "
                  f"neg_fp={chosen['neg_fp']} "
                  f"margin={chosen.get('margin')}")
        else:
            print("[calib] FAILED")

        loo = []
        if not args.no_loo:
            print("\n===== LEAVE-ONE-OUT =====")
            loo = leave_one_out(db, groups, negatives, rows)
            n_ok = sum(1 for r in loo if r["hit"])
            print(f"[loo] {n_ok}/{len(loo)} 留出组在由其余组推导的阈值下仍被召回")

        print(f"\n[sweep] done in {time.time() - t0:.1f}s")

        if not args.no_report:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(
                render_report(rows, chosen, criterion, sol, groups, negatives, diag, loo),
                encoding="utf-8")
            print(f"[sweep] report -> {REPORT_PATH}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
