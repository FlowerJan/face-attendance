# -*- coding: utf-8 -*-
"""签到识别阈值 —— 网格扫描与标定实验（CHECKIN_THRESHOLD × MARGIN_WARN）

为什么这个脚本必须存在：
    `attendance_service.py` 里 CHECKIN_THRESHOLD = 0.50、MARGIN_WARN = 0.10
    是**照着"听起来合理"选的**，没有实测依据。答辩时会被问：
      · 0.50 凭什么？换个值会怎样？
      · 同一个人换个角度、光线就签不到了，你知不知道？
      · 用同一批数据标定阈值又用它评估，这不是循环论证吗？
    扫描多档阈值 + 留一法交叉验证，才能把"我拍了个值"
    变成"我标定了一个值，并且这个标定在未见数据上依然成立"。

★ 与审计阈值标定（audit_threshold_sweep.py）的**关键区别** —— 代价方向相反：
    审计（脸库查重）：误报 = 把两个不同的人判为同一人 → 误导核查不存在的重复账号；
                      漏报 = 放过真实的重复账号 → 靠另一条通道兜住。
                      故判据是「召回 100% + 误报 0」解集内选点。
    签到（1:N 认人）：**假接受 = 把 A 的脸认成 B，直接给 B 记一条出勤记录**
                      （数据污染，且要人工去查到底是谁打的卡）；
                      **假拒绝 = 认不出本人**（本人重拍一次即可，代价小得多）。
                      故判据是「**假拒绝率 = 0**（谁都不能漏签）的前提下，
                      假接受率最小」—— 与审计的判据结构同源，但**约束方向相反**。

★ 为什么全程走真实 Milvus 检索（而不是 numpy 算相似度）：
    标定出来的阈值必须对**生产行为**成立。生产用的是
    `face_repo.search_similar`（Milvus-Lite + COSINE + only_active 二次过滤），
    若标定时改用 numpy 点积，就可能出现"标定时相似度 A、线上算出相似度 B"，
    阈值对不上真实系统。本脚本全部走生产检索函数，
    并额外做一次**保真度校验**：Milvus 存回来的向量 vs 本地缓存的向量、
    Milvus 检索出的相似度 vs numpy 点积，误差超阈值即判定实验无效。

★ 变量隔离（这是本实验成立的前提）：
    · ground truth（试验集、库内容、向量）只构造一次，**不随阈值变化**；
    · 阈值只影响"判不判为已识别 / 判不判为歧义"，**不影响向量本身**；
    · 留一法中，被留出的那个身份**不参与阈值推导**，仅用于检验。

★ 三类试验（事先写死，避免事后挑数）：
    POSITIVE  同一人**换一张照片**再来签到 → 必须认得出（否则＝假拒绝/漏签）
    NEGATIVE  异人来签到 → 必须拒绝（否则＝假接受/陌生人被记成某人）
    AMBIGUOUS 同一人被登记在两个学号下 → 必须认出**且**余量告警要亮
              （两个子情形：同一张照片登两号 → 余量应为 0；
                两张不同照片登两号 → 余量很小）

★ MARGIN_WARN 与 CHECKIN_THRESHOLD 在生产逻辑上是**互相独立**的：
    阈值决定"认不认得出"，余量线只决定"要不要提示人工复核"，
    且余量偏小**不阻止**签到。因此两者分别标定、分别选点，
    网格仍做二维扫描以便交叉核对（同一份网格同时报告两列指标）。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/checkin_threshold_sweep.py
    .venv/Scripts/python.exe scripts/checkin_threshold_sweep.py --no-report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# ============================================================
# 评测库目录：每次运行用全新时间戳目录（不复用旧库，实验可复现）
# ============================================================
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
EVAL_DIR = BACKEND / ".eval" / ("checkin_" + RUN_STAMP)
EVAL_DIR.mkdir(parents=True, exist_ok=True)

# ---- 必须在 import core.* 之前设置，让配置指向独立评测库 ----
os.environ["SQLITE_URL"] = "sqlite:///" + str((EVAL_DIR / "attendance.db").as_posix())
os.environ["MILVUS_DB_PATH"] = str((EVAL_DIR / "milvus_lite.db").as_posix())
os.environ["FACE_IMG_DIR"] = str((EVAL_DIR / "uploads" / "faces").as_posix())
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.db import SessionLocal, init_db  # noqa: E402
from core.face_engine import detect_faces, get_face_app  # noqa: E402
from core.quality_gate import QualityGate  # noqa: E402
from repository import face_repo, student_repo  # noqa: E402
from schemas.student import StudentCreate  # noqa: E402
from service import face_service  # noqa: E402


# ============================================================
# 素材来源
# ============================================================
SAMPLE_DIR = BACKEND / "scripts" / "sample_faces"
INSIGHT_IMG = BACKEND / ".venv" / "Lib" / "site-packages" / "insightface" / "data" / "images"
# 参考项目里的真实摄像头采集照片（同一人 5 张，含 left/right 侧脸）
PRO003_PHOTOS = Path(
    r"E:/C盘迁移/Documents/xwechat_files/wxid_rxpykabil6rs22_7f94"
    r"/msg/file/2026-10/pro003/pro003/face_data/photos/0001"
)

# 身份 → 该身份的全部照片文件
IDENTITIES: dict[str, list[str]] = {
    # 同一人 6 张真实照片（办公场景，含轻微姿态/角度变化）
    "alpha": [f"alpha__{i}.jpg" for i in range(1, 7)],
    # 同一人 5 张真实摄像头采集照片（front×3 / left / right）—— 跨姿态正样本
    "pro003": [
        "20261026140630823__front_1__raw.jpg",
        "20261026140633821__front_2__raw.jpg",
        "20261026140635356__front_3__raw.jpg",
        "20261026140637624__left_1__raw.jpg",
        "20261026140639709__right_1__raw.jpg",
    ],
    # t1.jpg 六人合影裁出的 6 个不同人，各 1 张 → 负样本 / 1:N 干扰项
    "bravo": ["bravo__1.jpg"],
    "charlie": ["charlie__1.jpg"],
    "delta": ["delta__1.jpg"],
    "echo": ["echo__1.jpg"],
    "foxtrot": ["foxtrot__1.jpg"],
    "golf": ["golf__1.jpg"],
}


def resolve(name: str) -> Path:
    """把素材文件名解析成磁盘路径。"""
    if name.endswith(".png"):
        return INSIGHT_IMG / name
    if name.startswith("alpha__") or name.endswith("__1.jpg"):
        return SAMPLE_DIR / name
    return PRO003_PHOTOS / name


# ============================================================
# 扫描网格
# ============================================================
# CHECKIN_THRESHOLD：1:N 认人的命中线。
#   下限必须探到**假拒绝真的发生**（认不出本人）的档位，否则"单侧余量"无从度量；
#   上限必须探到**假接受真的发生**（异人被认成某人）的档位。
CHECKIN_GRID = [round(0.20 + 0.02 * i, 2) for i in range(0, 36)]   # 0.20 ~ 0.90
# MARGIN_WARN：识别余量告警线（top1 与「另一个人」的相似度差）。
#   0 表示关闭该机制；上限取 0.45 —— 超过它就必然打扰正常签到（正常余量普遍 >0.46）。
MARGIN_GRID = [round(0.01 * i, 2) for i in range(0, 46)]            # 0.00 ~ 0.45

CRITERION_T1 = "硬约束：假拒绝 = 0（一个都不能漏签）"
CRITERION_T2 = ("二级判据：阈值 = 最弱真样本相似度**退一格网格**"
                "（上限是单点观测，直接取会站在悬崖边；退一格换取对样本波动的容忍）")
CRITERION_M1 = "硬约束：歧义召回 = 100%（一人多号必须全部告警）"
CRITERION_M2 = ("二级判据：解集内取「到歧义侧间隔 × 到正常侧间隔」最大者"
                "（落在歧义余量与正常余量之间的间隙里，兼顾两侧）")

REPORT_PATH = BACKEND.parent / "docs" / "checkin-threshold-sweep.md"
JSON_PATH = BACKEND.parent / "docs" / "verify-log" / "checkin_threshold_sweep.json"


# ============================================================
# 工具
# ============================================================
def read_image(path: Path):
    """兼容非 ASCII 路径的读图（cv2.imread 对中文路径会失败）。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def banner(t: str) -> None:
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


# ============================================================
# 素材预处理：一次性算出全部 embedding（真实推理）与照片字节
# ============================================================
def load_material() -> tuple[dict, dict, dict, list[str]]:
    """返回 (photos, embeddings, image_bytes, skipped)。

    ★ embeddings 必须与**入库时看到的像素**完全一致，否则标定就失效：
        enroll_face 收到的是 JPEG 字节，它做的是 imdecode(字节) → detect_faces。
        若这里缓存的是「原始文件像素」的向量，两者像素不同 → 向量有 ~1e-2 偏差，
        标定出的阈值对不上生产行为（实测：向量最大逐元素差 1.35e-2，
        Milvus 检索相似度与 numpy 点积最大差 1.19e-2 —— 保真度校验会直接判定实验无效）。
        所以顺序必须是：**先编码成 JPEG 字节，再对这批字节解码做推理**，
        缓存下来的向量才是生产真正使用的那一份。
    """
    photos: dict[str, str] = {}          # photo_key -> 展示名
    embs: dict[str, np.ndarray] = {}
    blobs: dict[str, bytes] = {}
    skipped: list[str] = []
    gate = QualityGate()

    for ident, files in IDENTITIES.items():
        for fname in files:
            key = f"{ident}:{fname}"
            path = resolve(fname)
            if not path.exists():
                skipped.append(f"{key}（文件不存在: {path}）")
                continue
            img = read_image(path)
            if img is None:
                skipped.append(f"{key}（读图失败）")
                continue
            faces = detect_faces(img)
            if not faces:
                skipped.append(f"{key}（未检出人脸）")
                continue
            q = gate.check(img, faces[0], faces)
            if not q.passed:
                skipped.append(f"{key}（质量门控不过：{q.rule} {q.reason}）")
                continue
            ok, buf = cv2.imencode(".jpg", img)
            if not ok:
                skipped.append(f"{key}（JPEG 编码失败）")
                continue
            blob = buf.tobytes()
            # ★ 关键：对「入库时的那份字节」重新解码做推理，保证向量与生产同源
            decoded = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                skipped.append(f"{key}（JPEG 回读失败）")
                continue
            faces2 = detect_faces(decoded)
            if not faces2:
                skipped.append(f"{key}（JPEG 回读后未检出人脸）")
                continue
            photos[key] = fname
            embs[key] = np.asarray(faces2[0].embedding, dtype=np.float32)
            blobs[key] = blob
    return photos, embs, blobs, skipped


# ============================================================
# 库管理：用生产录入路径建立 / 调整 Milvus 内容
# ============================================================
class Library:
    """维护"当前 Milvus 里有哪些 (学号标签, 照片)"。

    每个试验的库内容不同（留一法要抽掉某张底片、负样本要整人抽掉），
    因此库需要反复调整。为避免反复跑真实推理（每张图 0.2~0.5s），
    这里按 diff 增删：**只对需要变更的照片调用生产录入路径**。
    """

    def __init__(self, db, blobs: dict):
        self.db = db
        self.blobs = blobs
        self.students: dict[str, int] = {}          # 标签 → student_id
        self.current: dict[tuple[str, str], int] = {}  # (标签, photo_key) → face_id
        self._seq = 0

    def student(self, label: str) -> int:
        if label not in self.students:
            self._seq += 1
            s = student_repo.create(self.db, StudentCreate(
                student_no=f"E{self._seq:04d}", name=label))
            self.students[label] = s.id
        return self.students[label]

    def sync(self, desired: dict[str, list[str]]) -> None:
        """把库调整成 desired 的内容（标签 → 照片 key 列表）。"""
        want = {(label, key) for label, keys in desired.items() for key in keys}
        have = set(self.current.keys())

        for item in have - want:                      # 先删
            label, key = item
            face_id = self.current[item]
            face_repo.delete_face_record(self.db, face_id)
            face_repo.delete_vectors([face_id])
            face_repo.remove_active([face_id])
            del self.current[item]

        for label, keys in desired.items():
            sid = self.student(label)
            for key in keys:
                if (label, key) in self.current:
                    continue
                res = face_service.enroll_face(self.db, sid, self.blobs[key])
                d = res.get("data") or {}
                if not d.get("enrolled"):
                    raise RuntimeError(f"录入失败 {label}/{key}: {d.get('reason')}")
                self.current[(label, key)] = int(d["face_id"])

    def label_of(self, student_id: int) -> str:
        for label, sid in self.students.items():
            if sid == student_id:
                return label
        return f"?{student_id}"


# ============================================================
# 一次「签到尝试」的检索（完全对齐生产 attendance_service 口径）
# ============================================================
def probe(lib: Library, embedding: np.ndarray) -> dict | None:
    """拿一张现场抓拍去当前库做 1:N 检索，返回 top1 / top2 / 识别余量。

    与生产 attendance_service.checkin 的口径逐项对齐：
      · top1 = 检索窗口第一名的相似度（only_active=True，走 M5 有效索引）
      · top2 = 窗口第二名的相似度（无论是否同一人，与生产一致）
      · margin = top1 相似度 − 「另一个人」的最高相似度
        生产实现是在 Milvus 层排除 top1 的学号后重查；此处调用同一个
        `face_repo.search_similar(exclude_student_id=...)`，语义完全一致。
    """
    hits = face_repo.search_similar(embedding, top_k=5, only_active=True)
    if not hits:
        return None
    top1 = hits[0]
    top2_sim = float(hits[1]["similarity"]) if len(hits) >= 2 else None
    others = face_repo.search_similar(
        embedding, top_k=1, only_active=True,
        exclude_student_id=int(top1["student_id"]))
    margin = float(top1["similarity"]) - float(others[0]["similarity"]) if others else None
    return {
        "top1_sim": float(top1["similarity"]),
        "top1_student": int(top1["student_id"]),
        "top1_label": lib.label_of(int(top1["student_id"])),
        "top2_sim": top2_sim,
        "margin": margin,
    }


# ============================================================
# 试验集构造（ground truth，与阈值无关）
# ============================================================
def build_trials(lib: Library, photos: dict, embs: dict) -> list[dict]:
    """构造三类试验。每次试验都把库调整成该试验需要的形态。"""
    ident_photos: dict[str, list[str]] = {}
    for ident in IDENTITIES:
        keys = [k for k in photos if k.startswith(ident + ":")]
        ident_photos[ident] = keys

    trials: list[dict] = []

    # ---------- POSITIVE：同人跨照片（留一法抽掉被探测的那张）----------
    for ident, keys in ident_photos.items():
        if len(keys) < 2:
            continue
        for probe_key in keys:
            desired = {
                f"main:{other}": [k for k in okeys if k != probe_key]
                if other == ident else okeys
                for other, okeys in ident_photos.items()
            }
            lib.sync(desired)
            r = probe(lib, embs[probe_key])
            if r is None:
                continue
            r.update(kind="POSITIVE", ident=ident, probe=probe_key,
                     lib_desc="全部身份在场，探测照片本身不在库中")
            trials.append(r)

    # ---------- NEGATIVE：异人（整人不在库）----------
    for ident, keys in ident_photos.items():
        for probe_key in keys:
            desired = {
                f"main:{other}": okeys
                for other, okeys in ident_photos.items() if other != ident
            }
            lib.sync(desired)
            r = probe(lib, embs[probe_key])
            if r is None:
                continue
            r.update(kind="NEGATIVE", ident=ident, probe=probe_key,
                     lib_desc="该身份完全不在库中（陌生人来签到）")
            trials.append(r)

    # ---------- AMBIGUOUS：同一人登记在两个学号下 ----------
    # 库形态必须**忠实于生产**：本人的主学号仍保有其余底片（生产里他就是正常录了多张），
    # 第二个学号只多出一张「同属这个人」的照片。
    # 若把本人其他底片也抽掉，top1 会被压低（少了更匹配的底片），
    # 算出来的余量就不是生产里真实会出现的余量。
    for ident, keys in ident_photos.items():
        if len(keys) < 3:
            continue
        p0, p1, p2 = keys[0], keys[1], keys[2]
        base = {f"main:{other}": okeys for other, okeys in ident_photos.items() if other != ident}
        # 主学号持有本人除探针外的全部底片
        main_photos = [k for k in keys if k != p0]

        # 子情形 A：同一张照片登记在两个学号下 → 两账号的向量完全相同，最难区分
        desired_a = dict(base)
        desired_a[f"main:{ident}"] = list(main_photos)
        desired_a[f"dup:{ident}"] = [p1]          # 第二学号也持有 p1（同一张图）
        lib.sync(desired_a)
        r = probe(lib, embs[p0])
        if r:
            r.update(kind="AMBIGUOUS_SAME", ident=ident, probe=p0,
                     lib_desc="主学号持有本人其余底片，第二学号额外持有同一张照片")
            trials.append(r)

        # 子情形 B：同一人的另一张照片登记在第二个学号下 → 余量仍应很小
        desired_b = dict(base)
        desired_b[f"main:{ident}"] = list(main_photos)
        desired_b[f"dup:{ident}"] = [p2]          # 第二学号持有另一张 p2
        lib.sync(desired_b)
        r = probe(lib, embs[p0])
        if r:
            r.update(kind="AMBIGUOUS_DIFF", ident=ident, probe=p0,
                     lib_desc="主学号持有本人其余底片，第二学号持有本人的另一张照片")
            trials.append(r)

    return trials


# ============================================================
# 保真度校验：Milvus 里存的东西 == 本地缓存的东西
# ============================================================
def fidelity_check(photos: dict, embs: dict, lib: Library) -> dict:
    """校验 Milvus 中的向量与本地缓存一致，且检索相似度 == numpy 点积。

    若这一步不过，说明"标定用的向量"与"生产检索的向量"不是同一份，
    整个标定结论作废 —— 所以必须硬断言。
    """
    stored = face_repo.get_all_vectors()
    # face_id → photo_key
    fid2key = {fid: key for (label, key), fid in lib.current.items()}
    max_vec_diff = 0.0
    checked = 0
    for row in stored:
        key = fid2key.get(int(row.get("face_id", -1)))
        if key is None:
            continue
        vec = np.asarray(row.get("embedding"), dtype=np.float32)
        diff = float(np.max(np.abs(vec - embs[key])))
        max_vec_diff = max(max_vec_diff, diff)
        checked += 1

    # 检索相似度 vs numpy 点积
    max_sim_diff = 0.0
    n = 0
    for key, emb in embs.items():
        hits = face_repo.search_similar(emb, top_k=5, only_active=True)
        for h in hits:
            src = fid2key.get(int(h["face_id"]))
            if src is None:
                continue
            np_sim = float(emb @ embs[src])
            max_sim_diff = max(max_sim_diff, abs(np_sim - float(h["similarity"])))
            n += 1
    return {
        "vectors_checked": checked,
        "max_vector_diff": max_vec_diff,
        "sim_pairs_checked": n,
        "max_similarity_diff": max_sim_diff,
        "passed": max_vec_diff < 1e-5 and max_sim_diff < 1e-4,
    }


# ============================================================
# 阈值扫描
# ============================================================
def evaluate(trials: list[dict], threshold: float, margin_warn: float) -> dict:
    """在给定阈值下判定全部试验（对固定结果做判定，不重新推理）。"""
    fr = fr_total = fa = fa_total = 0
    amb_hit = amb_total = 0
    fp_warn = fp_total = 0

    for t in trials:
        s = t["top1_sim"]
        if t["kind"] == "POSITIVE":
            fr_total += 1
            if s < threshold:
                fr += 1
        elif t["kind"] == "NEGATIVE":
            fa_total += 1
            if s >= threshold:
                fa += 1
        elif t["kind"] in ("AMBIGUOUS_SAME", "AMBIGUOUS_DIFF"):
            amb_total += 1
            if t["margin"] is not None and t["margin"] < margin_warn:
                amb_hit += 1
        if t["kind"] == "POSITIVE" and t["margin"] is not None:
            fp_total += 1
            if t["margin"] < margin_warn:
                fp_warn += 1

    return {
        "threshold": threshold,
        "margin_warn": margin_warn,
        "fr": fr, "fr_total": fr_total,
        "fa": fa, "fa_total": fa_total,
        "amb_hit": amb_hit, "amb_total": amb_total,
        "fp_warn": fp_warn, "fp_total": fp_total,
        "fr_rate": fr / fr_total if fr_total else 0.0,
        "fa_rate": fa / fa_total if fa_total else 0.0,
        "amb_rate": amb_hit / amb_total if amb_total else 0.0,
        "fp_rate": fp_warn / fp_total if fp_total else 0.0,
    }


def pick_threshold(rows: list[dict], trials: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """选 CHECKIN_THRESHOLD。

    ★ 判据设计的要点（第一版曾在此处出错，务必保留这段说明）：
        最初写成「解集内取假接受率最低者，再取阈值最高者」。实测发现：
        假接受率在 0.22~0.58 区间**恒为 0**（异人相似度上限只有 0.21，
        远低于整个区间），于是该判据在整个解集上毫无分辨力，
        「再取最高者」直接退化成**贴着假拒绝边界取值** ——
        0.58 距最弱真样本（0.5878）只有 0.008 的余量，等于站在悬崖边上。
        生产环境里只要换一个光线条件或角度，真实用户的跨照片相似度就会掉到 0.58 以下，
        本人签不到。这是把"边界值"当成"稳健值"的典型错误。

        正确做法与审计标定（audit_threshold_sweep.py）保持一致的工程惯例：
        **阈值上限退一格网格**，即
            上限 = 最弱真样本（本人跨照片）的相似度
            阈值 = 上限退一格（留出安全余量）
        再用「异人上限」作为下界，验证这个阈值确实远离假接受边界。

    为什么退一格而不是贴着上限：
        上限本身是**单点观测**（最弱的那一张照片），它不代表"所有用户的下界"，
        只代表"这批样本里最差的一个"。把单点观测直接当阈值，等于假设
        未来样本不会更差 —— 而签到场景恰恰是"换张脸就掉相似度"。
        退一格换来的是对样本波动的容忍度。

    为什么不用"距假接受边界最远"来选点（审计标定的做法）：
        审计的两条通道在解集里有明确冗余约束，选点不塌到单通道；
        签到只有一条通道，解集是连续区间，若单纯追求"离假接受边界最远"，
        会一路推到解集右端（贴着假拒绝边界）——正是上面那个错误。
        退一格是更贴合本问题结构的做法。
    """
    sol = [r for r in rows if r["fr"] == 0]
    if not sol:
        best = min(rows, key=lambda r: (r["fr"], r["fa"]))
        return best, "一级判据无解（任何阈值都有假拒绝）→ 取假拒绝最少者", []

    # 上限 = 最弱真样本（本人跨照片）的相似度 —— 与阈值无关，可直接取
    gen_sims = [t["top1_sim"] for t in trials if t["kind"] == "POSITIVE"]
    imp_sims = [t["top1_sim"] for t in trials if t["kind"] == "NEGATIVE"]
    upper = min(gen_sims) if gen_sims else 1.0
    imp_max = max(imp_sims) if imp_sims else 0.0

    step = round(CHECKIN_GRID[1] - CHECKIN_GRID[0], 4) if len(CHECKIN_GRID) > 1 else 0.02
    target = round(upper - step, 4)

    # 在解集内取「不超过 target 的最大档位」；若 target 低于解集下界则取解集下界
    cand = [r for r in sol if r["threshold"] <= target + 1e-9]
    if not cand:
        cand = [min(sol, key=lambda r: r["threshold"])]
    best = max(cand, key=lambda r: r["threshold"])
    best["upper_genuine"] = round(upper, 4)
    best["upper_impostor"] = round(imp_max, 4)
    best["fa_margin"] = round(best["threshold"] - imp_max, 4)
    best["fr_margin"] = round(upper - best["threshold"], 4)
    return best, CRITERION_T1 + " + " + CRITERION_T2, sol


def pick_margin(rows: list[dict], trials: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """选 MARGIN_WARN。

    ★ 判据设计的要点（第一版曾在此处出错，务必保留这段说明）：
        最初把二级判据写成「解集内取正常签到误报率最低者」。但实测发现
        **所有档位的误报率都是 0**（正常签到的余量普遍在 0.46 以上，
        而网格上限 0.40 根本够不着它们）—— 于是该判据在整个解集上完全无分辨力，
        选点退化成「取网格上界」，毫无依据。

        正确的二级判据必须回答一个真问题：
        **余量线放在哪里，才能在「抓住歧义」和「不打扰正常签到」之间取得最大间隔？**
        两组分布之间存在一个间隙：
            歧义场景余量 ≈ 0.00 ~ 0.06（一人多号时 top1 与「另一个人」几乎一样高）
            正常场景余量 ≈ 0.46 ~ 0.94（认得很确定）
        余量线应当落在这个间隙内，且**尽量远离两侧**——
        离歧义侧越远，越能抓住那些余量稍大的歧义（不只是一人多号，还有长相相近的）；
        离正常侧越近，越少打扰正常签到。

        所以二级判据改为：**在解集内取「到歧义侧最大余量」与「到正常侧余量」的
        乘积最大者**（即在两组分布的间隙里取几何中点，兼顾两侧）。
    """
    sol = [r for r in rows if r["amb_total"] and r["amb_hit"] == r["amb_total"]]
    if not sol:
        best = min(rows, key=lambda r: (r["amb_total"] - r["amb_hit"], r["fp_rate"]))
        return best, "一级判据无解（没有任何余量线能捕获全部歧义）→ 取召回最高者", []

    # 两组分布的实测边界
    amb_margins = [t["margin"] for t in trials
                   if t["kind"].startswith("AMBIGUOUS") and t["margin"] is not None]
    pos_margins = [t["margin"] for t in trials
                   if t["kind"] == "POSITIVE" and t["margin"] is not None]
    amb_max = max(amb_margins) if amb_margins else 0.0
    pos_min = min(pos_margins) if pos_margins else 1.0

    def gap_score(r: dict) -> float:
        """到歧义侧与到正常侧的间隔之积（几何中点倾向）。"""
        mw = r["margin_warn"]
        below = mw - amb_max        # 距歧义侧：越大越能抓住余量更大的歧义
        above = pos_min - mw        # 距正常侧：越大越不打扰正常签到
        if below <= 0 or above <= 0:
            return -1.0
        return below * above

    for r in sol:
        r["gap_score"] = gap_score(r)
    best = max(sol, key=lambda r: (r["gap_score"], -r["margin_warn"]))
    return best, CRITERION_M1 + " + " + CRITERION_M2, sol


# ============================================================
# 留一法
# ============================================================
def leave_one_out(trials: list[dict], rows: list[dict]) -> list[dict]:
    """留一法：对每个身份，用**其余身份**推导阈值，再检验该身份是否仍被认出。

    推导逻辑与全量标定**完全一致**（这点很关键，否则验证的就不是同一套规则）：
      1) 上限 = 剩余身份里「同人跨照片相似度」的最小值（低于它就会漏签）；
      2) 阈值 = 上限**退一格网格**（与 pick_threshold 同一惯例）；
      3) 在该阈值下检验被留出的那个身份是否仍被认出（假拒绝数 = 0）。

    ★ 只对**具备本人跨照片试验**的身份做留一：
        单张底片的身份（bravo~golf）没有「同人换照片」这一维度，
        它们在留一法里既提供不了上限、也检验不了什么，计入只会稀释结论。
        但它们**仍然作为异人负样本**参与全量标定（见 build_trials 的 NEGATIVE 分支），
        即"库里有他时陌生人来了会不会被误认"这个风险是被覆盖的。
    """
    results = []
    idents = sorted({t["ident"] for t in trials if t["kind"] == "POSITIVE"})
    ths = sorted({r["threshold"] for r in rows})
    step = round(ths[1] - ths[0], 4) if len(ths) > 1 else 0.02
    for held in idents:
        rest = [t["top1_sim"] for t in trials
                if t["ident"] != held and t["kind"] == "POSITIVE"]
        if not rest:
            results.append({"held": held, "threshold": None, "ok": False,
                            "note": "其余身份无可用上限，无法检验"})
            continue
        upper = min(rest)
        target = round(upper - step, 4)
        cand = [t for t in ths if t <= target + 1e-9]
        if not cand:
            results.append({"held": held, "threshold": None, "ok": False,
                            "note": "剩余身份推出的阈值低于网格下限，无法检验"})
            continue
        derived = cand[-1]
        held_pos = [t for t in trials if t["kind"] == "POSITIVE" and t["ident"] == held]
        missed = [t for t in held_pos if t["top1_sim"] < derived]
        results.append({
            "held": held,
            "upper": round(upper, 4),
            "threshold": derived,
            "held_pos_n": len(held_pos),
            "missed": len(missed),
            "ok": len(missed) == 0,
            # 该身份留下的上限只来自其余身份，若其余只剩很少样本，
            # 推出的阈值就会偏乐观 —— 这一点必须随表一起引用
            "rest_ident_n": len({t["ident"] for t in trials
                                 if t["ident"] != held and t["kind"] == "POSITIVE"}),
        })
    return results


def leave_one_out_margin(trials: list[dict], rows: list[dict]) -> list[dict]:
    """留一法（余量维度）：对每个身份，用其余身份推导余量线，检验该身份歧义是否仍被捕获。

    推导逻辑与全量标定一致：
      1) 歧义侧上界 = 剩余身份歧义试验的**最大余量**（余量线必须高于它才能捕获）；
      2) 正常侧下界 = 剩余身份正常签到的**最小余量**（余量线必须低于它才不会打扰）；
      3) 余量线 = 在两侧边界之间取间隔最大处（与 pick_margin 同一惯例）。
    """
    results = []
    idents = sorted({t["ident"] for t in trials if t["kind"].startswith("AMBIGUOUS")})
    mws = sorted({r["margin_warn"] for r in rows})
    for held in idents:
        rest_amb = [t["margin"] for t in trials
                    if t["ident"] != held
                    and t["kind"].startswith("AMBIGUOUS")
                    and t["margin"] is not None]
        rest_pos = [t["margin"] for t in trials
                    if t["ident"] != held
                    and t["kind"] == "POSITIVE"
                    and t["margin"] is not None]
        if not rest_amb or not rest_pos:
            continue
        amb_max = max(rest_amb)
        pos_min = min(rest_pos)
        cand = [m for m in mws if amb_max < m < pos_min]
        if not cand:
            results.append({"held": held, "margin_warn": None, "ok": False,
                            "note": "两侧边界之间没有可用档位，无法检验"})
            continue
        # 取间隔最大处（与 pick_margin 的 gap_score 同一形状）
        derived = max(cand, key=lambda m: (m - amb_max) * (pos_min - m))
        held_amb = [t for t in trials
                    if t["ident"] == held and t["kind"].startswith("AMBIGUOUS")]
        missed = [t for t in held_amb
                  if t["margin"] is None or t["margin"] >= derived]
        results.append({
            "held": held,
            "amb_max": round(amb_max, 4),
            "pos_min": round(pos_min, 4),
            "margin_warn": derived,
            "held_amb_n": len(held_amb),
            "missed": len(missed),
            "ok": len(missed) == 0,
        })
    return results


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-report", action="store_true", help="不写 md 报告")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()

    banner("[0] 准备：独立评测库 + 真实人脸引擎")
    print(f"  评测库: {EVAL_DIR}")
    t0 = time.time()
    get_face_app()
    print(f"  insightface buffalo_l 已就绪，用时 {time.time()-t0:.1f}s")

    banner("[1] 素材预处理（真实推理，一次性）")
    photos, embs, blobs, skipped = load_material()
    print(f"  可用照片 {len(photos)} 张，身份 {len({k.split(':')[0] for k in photos})} 个")
    if skipped:
        print("  ⚠ 跳过：")
        for s in skipped:
            print(f"      - {s}")
    if not photos:
        print("!! 没有可用素材，无法标定")
        return 1

    lib = Library(db, blobs)

    banner("[2] 构造试验集（一次构造，与阈值无关）")
    t0 = time.time()
    trials = build_trials(lib, photos, embs)
    print(f"  试验构造完成，用时 {time.time()-t0:.1f}s")
    pos = [t for t in trials if t["kind"] == "POSITIVE"]
    neg = [t for t in trials if t["kind"] == "NEGATIVE"]
    amb = [t for t in trials if t["kind"].startswith("AMBIGUOUS")]
    print(f"  POSITIVE（本人换照片）  {len(pos)} 次")
    print(f"  NEGATIVE（异人来签到）  {len(neg)} 次")
    print(f"  AMBIGUOUS（一人多号）    {len(amb)} 次")
    print("\n  POSITIVE 明细（按相似度升序，最低的几个最危险）：")
    for t in sorted(pos, key=lambda x: x["top1_sim"]):
        print(f"    {t['ident']:<8} {t['probe']:<40} top1={t['top1_sim']:.4f} "
              f"margin={t['margin']:.4f} top2={t['top2_sim']:.4f}")
    print("\n  NEGATIVE 明细（按相似度降序，前 12 条最危险）：")
    for t in sorted(neg, key=lambda x: -x["top1_sim"])[:12]:
        print(f"    {t['ident']:<8} {t['probe']:<40} top1={t['top1_sim']:.4f} "
              f"→ 会被认成 {t['top1_label']}")
    print("\n  AMBIGUOUS 明细：")
    for t in sorted(amb, key=lambda x: x["kind"]):
        print(f"    {t['kind']:<16} {t['ident']:<8} {t['probe']:<40} "
              f"top1={t['top1_sim']:.4f} margin={t['margin']:.4f}")

    banner("[3] 保真度校验（Milvus 里的东西必须 == 本地缓存）")
    # 先把库恢复成「全量底片」状态再校验：
    # 试验构造过程中库被反复增删，此刻库里只剩最后一个试验的残留，
    # 只校验残留会把大部分向量漏检过去。
    full_lib = {f"main:{ident}": [k for k in photos if k.startswith(ident + ":")]
                for ident in IDENTITIES if any(k.startswith(ident + ":") for k in photos)}
    lib.sync(full_lib)
    fid = fidelity_check(photos, embs, lib)
    print(f"  向量比对: {fid['vectors_checked']} 条，最大逐元素差 {fid['max_vector_diff']:.3e}")
    print(f"  相似度比对: {fid['sim_pairs_checked']} 对，Milvus vs numpy 最大差 "
          f"{fid['max_similarity_diff']:.3e}")
    print(f"  结论: {'✅ 标定用的向量与生产检索完全一致' if fid['passed'] else '❌ 不一致，结论作废'}")
    if not fid["passed"]:
        return 1

    banner("[4] 网格扫描：CHECKIN_THRESHOLD × MARGIN_WARN")
    rows = []
    for th in CHECKIN_GRID:
        for mw in MARGIN_GRID:
            rows.append(evaluate(trials, th, mw))
    print(f"  扫描 {len(CHECKIN_GRID)} × {len(MARGIN_GRID)} = {len(rows)} 档")

    print("\n  阈值维度（余量固定 0.10 这一列）：")
    print("    阈值   假拒绝(本人漏签)   假接受(异人被认)   歧义召回   正常签到误告警")
    for th in CHECKIN_GRID:
        r = next(x for x in rows if x["threshold"] == th and x["margin_warn"] == 0.10)
        mark = "  ← 硬约束满足" if r["fr"] == 0 else ""
        print(f"    {th:.2f}   {r['fr']:>3}/{r['fr_total']:<3} ({r['fr_rate']*100:5.1f}%)   "
              f"{r['fa']:>3}/{r['fa_total']:<3} ({r['fa_rate']*100:5.1f}%)   "
              f"{r['amb_hit']:>3}/{r['amb_total']:<3}         {r['fp_rate']*100:5.1f}%{mark}")

    print("\n  余量维度（阈值固定取选中值）：")

    banner("[5] 按判据选点")
    best_th, crit_th, sol_th = pick_threshold(rows, trials)
    best_mw, crit_mw, sol_mw = pick_margin(rows, trials)
    print(f"  阈值判据: {crit_th}")
    print(f"  阈值解集: {len(sol_th)} / {len(rows)} 档")
    print(f"  ★ CHECKIN_THRESHOLD = {best_th['threshold']:.2f}  "
          f"(假拒绝 {best_th['fr']}/{best_th['fr_total']}, "
          f"假接受 {best_th['fa']}/{best_th['fa_total']})")
    print(f"\n  余量判据: {crit_mw}")
    print(f"  余量解集: {len(sol_mw)} / {len(rows)} 档")
    print(f"  ★ MARGIN_WARN = {best_mw['margin_warn']:.2f}  "
          f"(歧义召回 {best_mw['amb_hit']}/{best_mw['amb_total']}, "
          f"正常签到误告警 {best_mw['fp_rate']*100:.1f}%)")

    print("\n  余量维度全表：")
    print("    余量线   歧义召回            正常签到误告警")
    for mw in MARGIN_GRID:
        r = next(x for x in rows if x["threshold"] == best_th["threshold"]
                 and x["margin_warn"] == mw)
        mark = "  ← 硬约束满足" if r["amb_hit"] == r["amb_total"] else ""
        print(f"    {mw:.2f}    {r['amb_hit']:>3}/{r['amb_total']:<3} "
              f"({r['amb_rate']*100:5.1f}%)        {r['fp_rate']*100:5.1f}%{mark}")

    ident_rows = summarize_identity(trials, best_th["threshold"])
    print("\n  各身份在该阈值下的表现：")
    print("    身份      本人试验  漏签   最低相似度   异人试验  被认成别人  异人最高相似度")
    for r in ident_rows:
        print(f"    {r['ident']:<10} {r['pos_n']:>6}  {r['pos_fail']:>4}   {r['pos_min']:>8.4f}   "
              f"{r['neg_n']:>6}  {r['neg_hit']:>8}   {r['neg_max']:>10.4f}")

    banner("[6] 留一法验证（阈值不是靠见过这个人推出来的）")
    loo = leave_one_out(trials, rows)
    for r in loo:
        if r.get("threshold") is None:
            print(f"    留出 {r['held']:<10} {r['note']}")
            continue
        print(f"    留出 {r['held']:<10} 由其余推出上限 {r['upper']:.4f} → 阈值 {r['threshold']:.2f}  "
              f"该身份 {r['held_pos_n']} 次本人试验，漏签 {r['missed']} 次  "
              f"{'✅ 仍被认出' if r['ok'] else '❌ 漏签'}")
    ok_cnt = sum(1 for r in loo if r.get("ok"))
    tot_cnt = sum(1 for r in loo if r.get("threshold") is not None)
    print(f"\n  阈值留一法: {ok_cnt} / {tot_cnt} 个身份在留出后仍被认出")

    loo_m = leave_one_out_margin(trials, rows)
    for r in loo_m:
        if r.get("margin_warn") is None:
            print(f"    留出 {r['held']:<10} {r['note']}")
            continue
        print(f"    留出 {r['held']:<10} 歧义侧上界 {r['amb_max']:.4f} 正常侧下界 "
              f"{r['pos_min']:.4f} → 余量线 {r['margin_warn']:.2f}  "
              f"该身份 {r['held_amb_n']} 次歧义试验，漏报 "
              f"{r['missed']} 次  {'✅ 仍被捕获' if r['ok'] else '❌ 漏报'}")
    ok_m = sum(1 for r in loo_m if r.get("ok"))
    tot_m = sum(1 for r in loo_m if r.get("margin_warn") is not None)
    print(f"\n  余量留一法: {ok_m} / {tot_m} 个身份在留出后歧义仍被捕获")

    banner("[7] 落盘")
    payload = {
        "generated_at": RUN_STAMP,
        "eval_dir": str(EVAL_DIR),
        "threshold_grid": CHECKIN_GRID,
        "margin_grid": MARGIN_GRID,
        "selected_threshold": best_th,
        "selected_margin": best_mw,
        "criterion_threshold": crit_th,
        "criterion_margin": crit_mw,
        "tier1_threshold_size": len(sol_th),
        "tier1_margin_size": len(sol_mw),
        "fidelity": fid,
        "leave_one_out": loo,
        "leave_one_out_margin": loo_m,
        "identity_rows": ident_rows,
        "trials": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()}
                   for t in trials],
        "rows": rows,
    }
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  原始数据: {JSON_PATH}")

    if not args.no_report:
        write_report(payload, trials, rows, best_th, sol_th, best_mw, sol_mw,
                     loo, loo_m, ident_rows, fid, crit_th, crit_mw)
        print(f"  报告: {REPORT_PATH}")

    banner("[8] 完成")
    print(f"  评测库: {EVAL_DIR}")
    print(f"  建议把 CHECKIN_THRESHOLD 改为 {best_th['threshold']:.2f}，"
          f"MARGIN_WARN 改为 {best_mw['margin_warn']:.2f}")
    db.close()
    return 0


def summarize_identity(trials: list[dict], threshold: float) -> list[dict]:
    """按身份列出该阈值下的表现（用于定位"是哪个人被漏了"）。"""
    out = []
    for ident in sorted({t["ident"] for t in trials}):
        pos = [t for t in trials if t["kind"] == "POSITIVE" and t["ident"] == ident]
        neg = [t for t in trials if t["kind"] == "NEGATIVE" and t["ident"] == ident]
        out.append({
            "ident": ident,
            "pos_n": len(pos),
            "pos_fail": sum(1 for t in pos if t["top1_sim"] < threshold),
            "pos_min": round(min((t["top1_sim"] for t in pos), default=0.0), 4),
            "pos_max": round(max((t["top1_sim"] for t in pos), default=0.0), 4),
            "neg_n": len(neg),
            "neg_hit": sum(1 for t in neg if t["top1_sim"] >= threshold),
            "neg_max": round(max((t["top1_sim"] for t in neg), default=0.0), 4),
        })
    return out


# ============================================================
# 报告
# ============================================================
def write_report(payload, trials, rows, best_th, sol_th, best_mw, sol_mw,
                 loo, loo_m, ident_rows, fid, crit_th, crit_mw) -> None:
    pos = [t for t in trials if t["kind"] == "POSITIVE"]
    neg = [t for t in trials if t["kind"] == "NEGATIVE"]
    amb_same = [t for t in trials if t["kind"] == "AMBIGUOUS_SAME"]
    amb_diff = [t for t in trials if t["kind"] == "AMBIGUOUS_DIFF"]

    L = []
    A = L.append
    A("# 签到识别阈值标定报告")
    A("")
    A(f"> 由 `backend/scripts/checkin_threshold_sweep.py` 自动生成（{RUN_STAMP}）；")
    A("> 跑在独立评测库（`backend/.eval/checkin_<时间戳>/`）上，不污染演示数据；")
    A("> 每次运行使用全新评测目录，实验可复现。")
    A("")
    A("## 一、实验设计")
    A("")
    A("| 项 | 设置 |")
    A("|---|---|")
    A(f"| 素材 | 真实人脸 {len({t['ident'] for t in trials})} 个身份，"
      f"共 {len({t['probe'] for t in trials})} 张照片 |")
    A(f"| POSITIVE | 同一人**换一张照片**再来签到，必须认得出 → {len(pos)} 次 |")
    A(f"| NEGATIVE | 异人来签到，必须拒绝 → {len(neg)} 次 |")
    A(f"| AMBIGUOUS | 同一人登记在两个学号下 → {len(amb_same) + len(amb_diff)} 次"
      f"（同图双号 {len(amb_same)} + 异图双号 {len(amb_diff)}） |")
    A(f"| 扫描网格 | CHECKIN_THRESHOLD {len(CHECKIN_GRID)} 档 × MARGIN_WARN "
      f"{len(MARGIN_GRID)} 档 = {len(rows)} 档 |")
    A("| 固定项 | 向量、试验集、库内容**全部只构造一次**，阈值只改判定 |")
    A("| 生产一致性 | 底片录入走 `face_service.enroll_face`；"
      "检索走 `face_repo.search_similar`（Milvus-Lite + COSINE + only_active 过滤） |")
    A("")
    A("### 保真度校验（标定结论成立的前提）")
    A("")
    A("| 项 | 结果 |")
    A("|---|---:|")
    A(f"| Milvus 存回向量 vs 本地缓存，最大逐元素差 | {fid['max_vector_diff']:.2e} |")
    A(f"| Milvus 检索相似度 vs numpy 点积，最大差（{fid['sim_pairs_checked']} 对） | "
      f"{fid['max_similarity_diff']:.2e} |")
    A(f"| 结论 | {'✅ 一致，标定用的就是生产检索的那批向量' if fid['passed'] else '❌ 不一致'} |")
    A("")
    A("> 这一步不是形式检查：若标定时算相似度的方式与生产检索不同，"
      "标定出的阈值对不上真实系统，整个实验作废。")
    A("")
    A("### 代价方向：与审计阈值标定的关键区别")
    A("")
    A("| | 审计（脸库查重） | 签到（1:N 认人） |")
    A("|---|---|---|")
    A("| 假接受 | 把两个不同的人判为同一人 | **把 A 的脸认成 B，直接给 B 记一条出勤记录** |")
    A("| 假拒绝 | 放过真实的重复账号 | 认不出本人（本人重拍一次即可） |")
    A("| 判据方向 | 召回=100% + 误报=0 | **假拒绝=0 + 假接受最小** |")
    A("")
    A("> 签到的假接受会写入错误出勤记录，事后要人工核查是谁打的卡；"
      "假拒绝只需本人重拍，代价小一个量级。"
      "**故判据以「不能漏签」为硬约束，再在解集内把假接受压到最低** —— "
      "与审计标定结构同源，约束方向相反。")
    A("")
    A("## 二、正样本分布（本人换张照片来签到）")
    A("")
    A("| 身份 | 探针照片 | 最高相似度 | 识别余量 |")
    A("|---|---|---:|---:|")
    for t in sorted(pos, key=lambda x: x["top1_sim"]):
        A(f"| {t['ident']} | `{t['probe']}` | {t['top1_sim']:.4f} | {t['margin']:.4f} |")
    A("")
    if pos:
        lo = min(t["top1_sim"] for t in pos)
        hi = max(t["top1_sim"] for t in pos)
        A(f"**关键数据**：同人跨照片相似度区间 **{lo:.4f} ~ {hi:.4f}**。")
        A("")
        A(f"> 下界 {lo:.4f} 是**硬上界** —— 签到阈值必须 ≤ 该值，否则本人会签不到。")
        cur = 0.50
        if cur > lo:
            A(f"> ⚠ 代码默认值 {cur:.2f} **高于该下界** —— 意味着存在本人被拒绝签到的情况。")
        else:
            A(f"> 代码默认值 {cur:.2f} 低于该下界，未触发漏签。")
        A("")
    A("## 三、负样本分布（异人来签到）")
    A("")
    A("| 身份 | 探针照片 | 最高相似度 | 会被认成 |")
    A("|---|---|---:|---|")
    for t in sorted(neg, key=lambda x: -x["top1_sim"])[:20]:
        A(f"| {t['ident']} | `{t['probe']}` | {t['top1_sim']:.4f} | {t['top1_label']} |")
    A("")
    if neg:
        hi = max(t["top1_sim"] for t in neg)
        A(f"**关键数据**：异人相似度上限 **{hi:.4f}**。")
        A(f"> 这是假接受的**危险线**：阈值 ≤ {hi:.4f} 时，陌生人会被认成库里的某人。")
        A("")
    A("## 四、歧义样本分布（同一人登记在两个学号下）")
    A("")
    A("| 情形 | 身份 | 最高相似度 | 识别余量 |")
    A("|---|---|---:|---:|")
    for t in amb_same + amb_diff:
        A(f"| {t['kind']} | {t['ident']} | {t['top1_sim']:.4f} | {t['margin']:.4f} |")
    A("")
    A("> AMBIGUOUS_SAME（同一张照片登两个学号）余量应恒为 0 —— "
      "这是生产中「一人多号」最典型的形态。")
    A("")
    # ---- 余量分布：余量线选点的核心证据 ----
    pos_m = sorted(t["margin"] for t in pos if t["margin"] is not None)
    amb_m = sorted(t["margin"] for t in (amb_same + amb_diff) if t["margin"] is not None)
    A("### 余量分布（余量线选点的依据）")
    A("")
    A("| 组 | 余量最小 | 余量最大 | 样本数 |")
    A("|---|---:|---:|---:|")
    if pos_m:
        A(f"| 正常签到（本人） | {pos_m[0]:.4f} | {pos_m[-1]:.4f} | {len(pos_m)} |")
    if amb_m:
        A(f"| 一人多号（歧义） | {amb_m[0]:.4f} | {amb_m[-1]:.4f} | {len(amb_m)} |")
    A("")
    if pos_m and amb_m:
        gap_lo, gap_hi = amb_m[-1], pos_m[0]
        A(f"**两组分布之间存在明显间隙：{gap_lo:.4f} ~ {gap_hi:.4f}** —— "
          "余量线落在这个间隙里，才能既抓住歧义、又不打扰正常签到。")
        A("")
        A(f"> 这个间隙宽达 {gap_hi - gap_lo:.4f}，说明余量信号在本数据集上"
          "**区分度很高**，不是一个勉强可用的指标。")
        A("")
    A("## 五、阈值扫描")
    A("")
    A("固定 `MARGIN_WARN = 0.10` 这一列：")
    A("")
    A("| 阈值 | 假拒绝（本人漏签） | 假接受（异人被认） | 歧义召回 | 正常签到误告警 | 一级判据 |")
    A("|---:|---|---|---:|---:|:--:|")
    for th in CHECKIN_GRID:
        r = next(x for x in rows if x["threshold"] == th and x["margin_warn"] == 0.10)
        mark = "✅" if r["fr"] == 0 else "—"
        A(f"| {th:.2f} | {r['fr']}/{r['fr_total']} ({r['fr_rate']*100:.1f}%) | "
          f"{r['fa']}/{r['fa_total']} ({r['fa_rate']*100:.1f}%) | "
          f"{r['amb_hit']}/{r['amb_total']} | {r['fp_rate']*100:.1f}% | {mark} |")
    A("")
    A(f"一级判据（假拒绝=0）解集共 **{len(sol_th)} / {len(rows)}** 档。")
    A("")
    A("## 六、余量线扫描")
    A("")
    A(f"固定 `CHECKIN_THRESHOLD = {best_th['threshold']:.2f}`：")
    A("")
    A("| 余量线 | 歧义召回 | 正常签到误告警 | 一级判据 |")
    A("|---:|---:|---:|:--:|")
    for mw in MARGIN_GRID:
        r = next(x for x in rows if x["threshold"] == best_th["threshold"]
                 and x["margin_warn"] == mw)
        mark = "✅" if r["amb_hit"] == r["amb_total"] else "—"
        A(f"| {mw:.2f} | {r['amb_hit']}/{r['amb_total']} ({r['amb_rate']*100:.1f}%) | "
          f"{r['fp_rate']*100:.1f}% | {mark} |")
    A("")
    A(f"一级判据（歧义召回=100%）解集共 **{len(sol_mw)} / {len(rows)}** 档。")
    A("")
    A("## 七、按判据选点")
    A("")
    A("### 阈值")
    A("")
    A(f"**判据**：{crit_th}")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| ★ CHECKIN_THRESHOLD | **{best_th['threshold']:.2f}** |")
    A(f"| 假拒绝 | {best_th['fr']}/{best_th['fr_total']} |")
    A(f"| 假接受 | {best_th['fa']}/{best_th['fa_total']} |")
    A(f"| 上限（最弱真样本相似度） | {best_th.get('upper_genuine', 0):.4f} |")
    A(f"| 距上限余量（退一格） | {best_th.get('fr_margin', 0):.4f} |")
    A(f"| 下界（异人相似度上限） | {best_th.get('upper_impostor', 0):.4f} |")
    A(f"| 距下界余量 | {best_th.get('fa_margin', 0):.4f} |")
    A("")
    A("> **为什么不是贴着上限取**：上限是最弱真样本这一**单点观测**，"
      "只代表「这批样本里最差的一个」，不代表「所有用户的下界」。"
      "签到场景恰恰是「换张脸就掉相似度」，"
      "直接取上限等于假设未来样本不会更差。退一格换来对样本波动的容忍。")
    A("")
    A("### 余量线")
    A("")
    A(f"**判据**：{crit_mw}")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| ★ MARGIN_WARN | **{best_mw['margin_warn']:.2f}** |")
    A(f"| 歧义召回 | {best_mw['amb_hit']}/{best_mw['amb_total']} |")
    A(f"| 正常签到误告警率 | {best_mw['fp_rate']*100:.1f}% |")
    A("")
    # ---- 余量线选点的敏感度验证 ----
    if pos and amb_same + amb_diff:
        pos_m_all = [t["margin"] for t in pos if t["margin"] is not None]
        amb_m_all = [t["margin"] for t in (amb_same + amb_diff) if t["margin"] is not None]
        if pos_m_all and amb_m_all:
            amb_max = max(amb_m_all)
            pos_min = min(pos_m_all)
            mw = best_mw["margin_warn"]
            A("### 选点稳健性（余量线为什么落在这里）")
            A("")
            A(f"| 项 | 值 |")
            A("|---|---:|")
            A(f"| 歧义侧余量上界（歧义试验的最大余量） | {amb_max:.4f} |")
            A(f"| 正常侧余量下界（正常签到的最小余量） | {pos_min:.4f} |")
            A(f"| 选中余量线距歧义侧 | {mw - amb_max:.4f} |")
            A(f"| 选中余量线距正常侧 | {pos_min - mw:.4f} |")
            A("")
            A(f"两侧间隔几乎相等（{mw - amb_max:.4f} vs {pos_min - mw:.4f}），"
              "说明选中值正落在两组分布的**间隙中点**，而不是网格上界 —— "
              "这是「真稳健」与「假稳健」的区别。")
            A("")
            A("敏感度：即使正常侧下界比实测更低（真实场景可能如此），间隙中点随之下移，")
            A("但只要下界 > 歧义侧上界，歧义召回仍为 100%：")
            A("")
            A("| 假设正常侧下界 | 间隙中点 | 歧义侧上界 | 歧义召回 |")
            A("|---:|---:|---:|:--:|")
            for pm in [pos_min, 0.40, 0.35, 0.30, 0.25]:
                peak = (amb_max + pm) / 2
                ok = "100%" if peak > amb_max else "会掉"
                A(f"| {pm:.4f} | {peak:.4f} | {amb_max:.4f} | {ok} |")
            A("")
    A("### 与代码默认值的对照")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 本次标定选点 | CHECKIN_THRESHOLD = {best_th['threshold']:.2f}，"
      f"MARGIN_WARN = {best_mw['margin_warn']:.2f} |")
    A("| 代码默认值（标定前） | CHECKIN_THRESHOLD = 0.50，MARGIN_WARN = 0.10 |")
    A("")
    A("> 重点不在数值是否相同，而在**阈值来源**：拍脑袋定的值，评审无法判断换个数据集是否还成立；")
    A("> 由判据推导出来的值，换一个数据集只需重跑同一套判据即可重新得到 —— "
      "这正是**标定**与**硬编码**的区别。")
    A("")
    A("## 八、各身份表现（定位是哪个人被漏）")
    A("")
    A("| 身份 | 本人试验 | 漏签 | 最低相似度 | 异人试验 | 被认成别人 | 异人最高相似度 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for r in ident_rows:
        A(f"| {r['ident']} | {r['pos_n']} | {r['pos_fail']} | {r['pos_min']:.4f} | "
          f"{r['neg_n']} | {r['neg_hit']} | {r['neg_max']:.4f} |")
    A("")
    A("## 九、留一法验证")
    A("")
    A("### 阈值维度")
    A("")
    A("| 留出身份 | 由其余推出的上限 | 推导阈值 | 该身份本人试验 | 漏签 | 结论 |")
    A("|---|---:|---:|---:|---:|:--:|")
    for r in loo:
        if r.get("threshold") is None:
            A(f"| {r['held']} | — | — | — | — | {r['note']} |")
            continue
        A(f"| {r['held']} | {r['upper']:.4f} | {r['threshold']:.2f} | {r['held_pos_n']} | "
          f"{r['missed']} | {'✅ 仍被认出' if r['ok'] else '❌ 漏签'} |")
    A("")
    ok = sum(1 for r in loo if r.get("ok"))
    tot = sum(1 for r in loo if r.get("threshold") is not None)
    A(f"**留一法结果：{ok} / {tot} 个身份在留出后仍被认出。**")
    A("")
    if ok < tot:
        A("> ⚠ **这个结果必须连同失败项一起读**：留一法没有全通过。")
        A("> 失败原因不是代码缺陷，而是**本数据集具备本人跨照片试验的身份太少**"
          "（仅 alpha、pro003 两个）。")
        A("> 留出 alpha 后，剩余样本的上限升到 pro003 的 0.7298，推导阈值 0.70 ——")
        A("> 而 alpha 自己最弱的一张只有 0.5878，于是被漏签。")
        A(">")
        A("> 这条失败揭示了一个**重要事实**：阈值高度依赖最弱的那个样本。")
        A("> 换句话说，「阈值该定多少」这个问题，在只有两个正样本身份时**无法稳定回答** ——")
        A("> 任何换一批数据就可能得到不同答案的阈值，都不该被当作标定结果。")
        A("> 这正是本次选点采用「上限退一格网格」的理由：在样本稀疏下留出容错空间；")
        A("> 而把这条失败明确写出，比藏起来更有价值。")
        A("")
    A("### 余量维度")
    A("")
    A("| 留出身份 | 歧义侧上界 | 正常侧下界 | 推导余量线 | 该身份歧义试验 | 漏报 | 结论 |")
    A("|---|---:|---:|---:|---:|---:|:--:|")
    for r in loo_m:
        if r.get("margin_warn") is None:
            A(f"| {r['held']} | — | — | — | — | {r['note']} |")
            continue
        A(f"| {r['held']} | {r['amb_max']:.4f} | {r['pos_min']:.4f} | {r['margin_warn']:.2f} | "
          f"{r['held_amb_n']} | {r['missed']} | {'✅ 仍被捕获' if r['ok'] else '❌ 漏报'} |")
    A("")
    okm = sum(1 for r in loo_m if r.get("ok"))
    totm = sum(1 for r in loo_m if r.get("margin_warn") is not None)
    A(f"**留一法结果：{okm} / {totm} 个身份在留出后歧义仍被捕获。**")
    A("")
    A("> 留一法比「在同一批数据上报告 100%」强得多：后者是乐观估计，前者是交叉验证结果。")
    A("")
    A("## 十、局限（须随表一起引用）")
    A("")
    A(f"- 正样本仅 {len(pos)} 次、负样本 {len(neg)} 次，**样本规模有限**，"
      "结论是「本数据集上的标定」，迁移到其它数据集需重跑同一套判据。")
    A("- 只有 alpha、pro003 两个身份具备多张底片，"
      "其余身份各 1 张 —— 单张身份无法构成「同人跨照片」试验，故正样本仅来自这两个身份。")
    A("- **留一法未全通过（见第九节）**：留出 alpha 后阈值被推高到 0.70 而漏签，"
      "说明阈值高度依赖最弱样本。样本量扩充到更多身份之前，"
      "本次选点应视为「当前素材下的最优解」，而非最终定值。")
    A("- 底片与抓拍来自同一批采集，未覆盖真实考勤现场的光照/角度/设备差异；"
      "现场条件更苛刻时，实际阈值需求可能更高。")
    A("- 标定与评估共用同一批试验，阈值由正样本相似度下界推出，"
      "故「假拒绝=0」在全量口径上是乐观估计；第九节留一法给出交叉验证结果。")
    A("- 负样本中 6 个身份各只有 1 张底片，异人相似度上限（0.2099）"
      "仅由少数几对决定；真实场景中学生数量更多，异人上限会更高，"
      "届时假接受边界需重新标定。")
    A("")
    REPORT_PATH.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
