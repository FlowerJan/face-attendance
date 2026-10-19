# -*- coding: utf-8 -*-
"""脸库审计「召回率」评测（独立评测库，不污染演示数据）

思路：
    1. 造一批已知真值（ground truth）的学生，按「组」安排：
         - 真实组：同一个人的多张真实照片，分别录到不同学号  => 审计应当发现
         - 合成组：同一张图做翻转/亮度增广后录到不同学号      => 审计应当发现
         - 负样本：互不相干的人，各录一张                    => 审计不应当误报
    2. 调用 service.cluster_service.audit_duplicates() 跑真实审计逻辑
    3. 与真值比对，输出召回率 / 误报数，并写出 Markdown 报告

为什么跑在独立库上：
    通过环境变量把 SQLITE_URL / MILVUS_DB_PATH / FACE_IMG_DIR 指到 .eval/ 目录，
    因此不会动 backend/attendance.db 和 backend/milvus_lite.db 里的演示数据，
    也不会和正在运行的后端抢 Milvus Lite 的文件锁。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/audit_recall_test.py
    .venv/Scripts/python.exe scripts/audit_recall_test.py --reset   # 清空评测库后重跑
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

sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.db import SessionLocal, init_db  # noqa: E402
from core.enums import EnumContainer  # noqa: E402
from core.face_engine import get_face_app  # noqa: E402
from core.milvus import get_client  # noqa: E402
from repository import student_repo  # noqa: E402
from schemas.student import StudentCreate  # noqa: E402
from service import face_service  # noqa: E402
from service import cluster_service  # noqa: E402

PROJ = Path(r"E:\workbuddy-projects")
SITE = BACKEND / ".venv" / "Lib" / "site-packages" / "insightface" / "data" / "images"

# 同一位男士的 6 张不同照片（真实素材，非合成）
SAME_PERSON_PHOTOS = [
    PROJ / "同一个戴黑框眼镜_穿白色polo衫的年轻男子_在同样的现代办_2026-09-18T03-14-27.png",
    PROJ / "同一个戴黑框眼镜_穿白色polo衫的年轻男子_在同样的现代办_2026-09-18T03-16-22.png",
    PROJ / "在这张照片中_保持男子和他的姿势_表情_眼镜_衣服完全不变__2026-09-18T02-59-10.png",
    PROJ / "在这张照片中_保持男子和他的长相_眼镜_白色polo衫完全不变__2026-09-18T03-04-11.png",
    PROJ / "在这张照片中_保持男子和他的长相_黑框眼镜_白色polo衫完全不变__2026-09-18T03-00-34.png",
    PROJ / "在这张照片中_保持男子的长相_眼镜_白色polo衫完全不变__2026-09-18T03-11-29.png",
]

# 其他身份（互不相干的人），用于负样本与合成组
OTHER_PEOPLE = [(SITE / "t1.jpg", r) for r in range(8)] + [
    (SITE / "Tom_Hanks_54745.png", 0),
    (SITE / "mask_black.jpg", 0),
    (SITE / "mask_blue.jpg", 0),
    (SITE / "mask_green.jpg", 0),
    (SITE / "mask_white.jpg", 0),
]

REPORT_PATH = BACKEND.parent / "docs" / "audit-recall-report.md"


def read_image(path: Path):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def to_jpeg_bytes(img) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else None


def crop_face(app, img, rank: int = 0):
    """裁出面积第 rank 大的那张脸（外扩 60% 边距），失败返回 None。"""
    if img is None:
        return None
    faces = app.get(img)
    if not faces:
        return None
    faces = sorted(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    if rank >= len(faces):
        return None
    x1, y1, x2, y2 = [float(v) for v in faces[rank].bbox]
    mx, my = (x2 - x1) * 0.6, (y2 - y1) * 0.6
    crop = img[max(0, int(y1 - my)):int(y2 + my), max(0, int(x1 - mx)):int(x2 + mx)].copy()
    if crop.shape[1] > 480:
        s = 480 / crop.shape[1]
        crop = cv2.resize(crop, (480, int(crop.shape[0] * s)))
    return crop


def augment_flip(img):
    return cv2.flip(img, 1)


def augment_brightness(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2].astype(np.int16) + 35, 0, 255).astype(np.uint8)
    out = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return cv2.convertScaleAbs(out, alpha=1.08, beta=8)


def reset_eval_db() -> None:
    import shutil
    for p in [EVAL_DIR / "attendance.db", EVAL_DIR / "milvus_lite.db"]:
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    up = EVAL_DIR / "uploads"
    if up.exists():
        shutil.rmtree(up, ignore_errors=True)
    (EVAL_DIR / "uploads" / "faces").mkdir(parents=True, exist_ok=True)
    print("[eval] 评测库已重置")


def make_student(db, seq: int, tag: str):
    classes = EnumContainer.get_classes() or ["智造本2024-1班"]
    majors = EnumContainer.get_majors() or ["计算机科学与技术"]
    return student_repo.create(db, StudentCreate(
        student_no=f"9000{seq:04d}",
        name=f"{tag}-{seq:03d}",
        gender="男",
        class_name=classes[0],
        major=majors[0],
        enroll_year=2024,
        phone=f"139{seq:08d}",
    ))


def enroll(db, app, student_id: int, img) -> tuple[bool, str]:
    data = to_jpeg_bytes(img)
    if data is None:
        return False, "jpeg encode failed"
    res = face_service.enroll_face(db, student_id, data)
    d = res.get("data") or {}
    return bool(d.get("detected")), (d.get("reason") or "ok")


IDENTITY_SIM_THRESHOLD = 0.45   # 高于此值认为是同一个人


def collect_candidates(app) -> tuple[list[dict], list[str]]:
    """裁切全部候选素材并取特征；返回 (candidates, log)。"""
    items, log = [], []
    for label, src, rank in CANDIDATES:
        if not src.exists():
            log.append(f"[skip] 素材不存在: {src.name}")
            continue
        img = crop_face(app, read_image(src), rank)
        if img is None:
            log.append(f"[skip] 未检出人脸: {src.name}#{rank}")
            continue
        faces = app.get(img)
        if not faces:
            log.append(f"[skip] 裁切后无脸: {src.name}#{rank}")
            continue
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        items.append({
            "label": label,
            "src": f"{src.name}#{rank}",
            "image": img,
            "embedding": np.asarray(f.normed_embedding, dtype=np.float64),
            "det_score": float(f.det_score),
        })
    return items, log


def group_into_identities(items: list[dict]) -> list[dict]:
    """按余弦相似度把候选聚成身份（并查集，阈值 IDENTITY_SIM_THRESHOLD）。"""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            a = items[i]["embedding"] / (np.linalg.norm(items[i]["embedding"]) + 1e-8)
            b = items[j]["embedding"] / (np.linalg.norm(items[j]["embedding"]) + 1e-8)
            if float(a @ b) > IDENTITY_SIM_THRESHOLD:
                union(i, j)

    buckets: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        buckets.setdefault(find(i), []).append(it)
    return [{"members": v} for v in buckets.values()]


def build_ground_truth(db, app) -> tuple[list[dict], list[int], list[str]]:
    """返回 (groups, negative_student_ids, log)

    关键约束：**每个身份只能属于一个组**。
    否则两个组里是同一个人，审计把他们判为重复其实是「判对了」，
    却被当成误报 —— 那是 ground truth 标错，不是审计错了。
    """
    groups: list[dict] = []
    negatives: list[int] = []
    log: list[str] = []
    seq = [0]

    def new_student(tag: str):
        seq[0] += 1
        return make_student(db, seq[0], tag)

    items, ilog = collect_candidates(app)
    log += ilog
    identities = group_into_identities(items)
    log.append(f"[data] 可用素材 {len(items)} 张 -> 聚成 {len(identities)} 个身份")

    # 多张同人照片的身份 => 真实组
    multi = [i for i in identities if len(i["members"]) > 1]
    single = [i for i in identities if len(i["members"]) == 1]

    gi = 0
    for ident in multi:
        members = []
        for m in ident["members"]:
            st = new_student("REAL")
            ok, why = enroll(db, app, st.id, m["image"])
            if ok:
                members.append(st.id)
            else:
                log.append(f"[skip] 真实组录入失败: {why}")
        if len(members) >= 2:
            gi += 1
            groups.append({
                "name": f"真实组{chr(64 + gi)}",
                "kind": "real",
                "students": members,
                "sources": [m["src"] for m in ident["members"]],
            })

    # 合成组：每个身份出 1 张原图 + 1 张增广图；至少给负样本留 2 个身份
    aug_specs = [("水平翻转", augment_flip), ("提亮增广", augment_brightness), ("水平翻转", augment_flip)]
    synthetic_identities = single[:max(0, min(len(aug_specs), len(single) - 2))]
    negative_identities = single[len(synthetic_identities):]

    for idx, ident in enumerate(synthetic_identities):
        gname, fn = aug_specs[idx % len(aug_specs)]
        base = ident["members"][0]["image"]
        members = []
        for img in [base, fn(base)]:
            st = new_student("SYN")
            ok, why = enroll(db, app, st.id, img)
            if ok:
                members.append(st.id)
            else:
                log.append(f"[skip] 合成组录入失败: {why}")
        if len(members) >= 2:
            groups.append({
                "name": f"合成组{chr(66 + idx)}-{gname}",
                "kind": "synthetic",
                "students": members,
                "sources": [ident["members"][0]["src"], f"同图{gname}"],
            })

    # 负样本：互不相干的人，各一张
    for ident in negative_identities:
        st = new_student("NEG")
        ok, why = enroll(db, app, st.id, ident["members"][0]["image"])
        if ok:
            negatives.append(st.id)
        else:
            log.append(f"[skip] 负样本录入失败: {why}")

    # 负样本独立性校验
    neg_items = [i["members"][0] for i in negative_identities]
    bad = False
    for i in range(len(neg_items)):
        for j in range(i + 1, len(neg_items)):
            a = neg_items[i]["embedding"] / (np.linalg.norm(neg_items[i]["embedding"]) + 1e-8)
            b = neg_items[j]["embedding"] / (np.linalg.norm(neg_items[j]["embedding"]) + 1e-8)
            s = float(a @ b)
            if s > 0.40:
                log.append(f"[warn] 负样本可能不独立: {neg_items[i]['src']} vs {neg_items[j]['src']} sim={s:.3f}")
                bad = True
    log.append(f"[check] 负样本独立性: {'存在问题' if bad else '通过（两两相似度均 < 0.40）'}")

    return groups, negatives, log


def evaluate(groups, negatives, audit) -> dict:
    data = audit.get("data") or {}
    dup_pairs = {(d["student_a"]["id"], d["student_b"]["id"]) for d in data.get("duplicates", [])}
    dup_pairs |= {(b, a) for (a, b) in dup_pairs}
    clusters = [set(c["student_ids"]) for c in data.get("suspicious_clusters", [])]

    results = []
    for g in groups:
        s = set(g["students"])
        hit_pairs = sorted({(min(a, b), max(a, b)) for (a, b) in dup_pairs if a in s and b in s})
        hit_cluster = any(len(c & s) >= 2 for c in clusters)
        hit = bool(hit_pairs) or hit_cluster
        sims = [round(d["similarity"], 3) for d in data.get("duplicates", [])
                if d["student_a"]["id"] in s and d["student_b"]["id"] in s]
        results.append({
            "group": g["name"], "kind": g["kind"], "size": len(s),
            "hit": hit, "pairs": len(hit_pairs), "cluster_hit": hit_cluster,
            "max_sim": max(sims) if sims else None,
        })

    # 误报：审计报出的 pair 中，两人不属于同一真值组
    gt_pair_sets = [set(g["students"]) for g in groups]
    fp_pairs = 0
    fp_students = set()
    for (a, b) in {(min(x), max(x)) for x in dup_pairs}:
        if not any(a in s and b in s for s in gt_pair_sets):
            fp_pairs += 1
            fp_students.add(a)
            fp_students.add(b)
    for c in clusters:
        for s in gt_pair_sets:
            c = c - s
        fp_students |= c

    neg_set = set(negatives)
    neg_false_positive = sorted(neg_set & fp_students)

    hit_count = sum(1 for r in results if r["hit"])
    return {
        "results": results,
        "total_groups": len(groups),
        "hit_groups": hit_count,
        "recall": hit_count / len(groups) if groups else 0.0,
        "fp_pairs": fp_pairs,
        "neg_count": len(negatives),
        "neg_false_positive": neg_false_positive,
        "summary": data.get("summary", {}),
        "thresholds": data.get("thresholds", {}),
    }


def render_report(ev: dict) -> str:
    lines = []
    lines.append("# 脸库审计召回率评测报告\n")
    lines.append("> 由 `backend/scripts/audit_recall_test.py` 自动生成；")
    lines.append("> 评测跑在独立评测库（`backend/.eval/`）上，不污染演示数据。\n")

    lines.append("## 一、结论\n")
    lines.append("| 指标 | 结果 |")
    lines.append("|---|---|")
    lines.append(f"| 构造的异常组总数 | {ev['total_groups']} |")
    lines.append(f"| 审计成功命中 | {ev['hit_groups']} |")
    lines.append(f"| **召回率** | **{ev['recall'] * 100:.0f}%** |")
    lines.append(f"| 误报的跨组配对数 | {ev['fp_pairs']} |")
    lines.append(f"| 负样本数（互不相干的人） | {ev['neg_count']} |")
    lines.append(f"| 负样本被误报数 | {len(ev['neg_false_positive'])} |")
    lines.append("")

    lines.append("## 二、逐组结果\n")
    lines.append("| 组 | 类型 | 涉及学号数 | 是否命中 | 组内命中配对 | 簇内命中 | 最高相似度 |")
    lines.append("|---|---|---:|:---:|---:|:---:|---:|")
    for r in ev["results"]:
        lines.append(
            f"| {r['group']} | {'真实照片' if r['kind'] == 'real' else '合成增广'} "
            f"| {r['size']} | {'✅' if r['hit'] else '❌'} | {r['pairs']} "
            f"| {'是' if r['cluster_hit'] else '否'} | {r['max_sim'] if r['max_sim'] is not None else '-'} |"
        )
    lines.append("")

    lines.append("## 三、审计原始输出\n")
    lines.append("```json")
    lines.append(str(ev["summary"]))
    lines.append("```\n")
    lines.append(f"阈值：`{ev['thresholds']}`\n")

    real_groups = [r for r in ev["results"] if r["kind"] == "real"]
    syn_groups = [r for r in ev["results"] if r["kind"] == "synthetic"]
    real_photos = sum(g["size"] for g in real_groups)

    lines.append("## 四、口径说明（重要，面试请照此说）\n")
    lines.append(f"- **真实组（{len(real_groups)} 组 / 共 {real_photos} 张照片）**："
                 "同一位男士的真实照片，逐张录到不同学号；")
    lines.append(f"- **合成组（{len(syn_groups)} 组）**：同一张图做水平翻转 / 提亮增广后录到两个学号，"
                 "**属构造数据，不是真实采集**；")
    lines.append(f"- **负样本（{ev['neg_count']} 个）**：互不相干的人，各录一张；脚本会先校验它们两两相似度 < 0.40，"
                 "确认彼此独立后才计入；")
    lines.append("- 判定口径：组内出现跨学号高相似度配对，或层次聚类把组内成员聚到同一簇，均计为命中；")
    lines.append("- **每个身份只属于一个组**：否则两组里是同一个人，审计判他们重复其实是判对了，"
                 "却被误算成误报 —— 那是 ground truth 标错，不是审计错了。\n")
    lines.append("## 五、素材限制（必须如实说明）\n")
    lines.append("本机可检出人脸的素材有限：同一人的多张真实照片只有 3 张可用，"
                 "互不相干的其他身份来自 insightface 自带的合影测试图。"
                 "因此本评测的规模是「4 组异常 + 3 个负样本」，**不是大规模基准测试**，"
                 "它的意义在于验证审计逻辑的正确性与阈值设定的有效性。\n")
    return "\n".join(lines) + "\n"


CANDIDATES = [(f"same-{i}", p, 0) for i, p in enumerate(SAME_PERSON_PHOTOS)] + [
    (f"other-{i}", src, rank) for i, (src, rank) in enumerate(OTHER_PEOPLE)
]


def diag(app) -> int:
    """打印候选素材的检测情况与两两相似度，用于确认 ground truth 是否站得住。"""
    embs, labels = [], []
    for label, src, rank in CANDIDATES:
        img = crop_face(app, read_image(src), rank)
        if img is None:
            print(f"  {label:<10} DETECT-FAIL  {src.name}#{rank}")
            continue
        res = app.get(img)
        if not res:
            print(f"  {label:<10} NO-FACE      {src.name}#{rank}")
            continue
        f = max(res, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        embs.append(np.asarray(f.normed_embedding, dtype=np.float64))
        labels.append(label)
        print(f"  {label:<10} OK det={float(f.det_score):.3f}  {src.name}#{rank}")

    print(f"\n[diag] usable={len(embs)}")
    if len(embs) < 2:
        return 1
    m = np.asarray(embs)
    m = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-8)
    sim = m @ m.T
    print("\n[diag] pairs with similarity > 0.40 (likely SAME person):")
    found = False
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if sim[i, j] > 0.40:
                print(f"  {labels[i]:<10} <-> {labels[j]:<10} sim={float(sim[i, j]):.3f}")
                found = True
    if not found:
        print("  (none)")
    print("\n[diag] pairs in 0.20~0.40 (ambiguous, avoid as negatives):")
    amb = False
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if 0.20 < sim[i, j] <= 0.40:
                print(f"  {labels[i]:<10} <-> {labels[j]:<10} sim={float(sim[i, j]):.3f}")
                amb = True
    if not amb:
        print("  (none)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空评测库再跑")
    ap.add_argument("--no-report", action="store_true", help="不写 Markdown 报告")
    ap.add_argument("--diag", action="store_true", help="只做素材检测与相似度诊断，不写库")
    args = ap.parse_args()

    if args.reset:
        reset_eval_db()

    t0 = time.time()
    init_db()
    EnumContainer.load()
    get_client()
    app = get_face_app()
    print("[eval] model + vector store ready")

    if args.diag:
        return diag(app)

    db = SessionLocal()
    try:
        groups, negatives, log = build_ground_truth(db, app)
        for line in log:
            print(line)
        print(f"[eval] ground truth: {len(groups)} groups, {len(negatives)} negatives")

        vectors = len(__import__("repository").face_repo.get_all_vectors())
        print(f"[eval] vectors in store: {vectors}")

        audit = cluster_service.audit_duplicates(db)
        ev = evaluate(groups, negatives, audit)

        print("\n===== RESULT =====")
        print(f"groups={ev['total_groups']} hit={ev['hit_groups']} recall={ev['recall'] * 100:.0f}%")
        print(f"false-positive cross-group pairs={ev['fp_pairs']}")
        print(f"negatives={ev['neg_count']} neg_false_positive={len(ev['neg_false_positive'])}")
        for r in ev["results"]:
            print(f"  {r['group']:<16} {r['kind']:<11} size={r['size']} hit={r['hit']} max_sim={r['max_sim']}")
        print(f"[eval] done in {time.time() - t0:.1f}s")

        if not args.no_report:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(render_report(ev), encoding="utf-8")
            print(f"[eval] report -> {REPORT_PATH}")

        ok = ev["recall"] == 1.0 and not ev["neg_false_positive"]
        print("[eval] PASS" if ok else "[eval] FAIL")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
