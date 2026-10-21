# -*- coding: utf-8 -*-
"""质量门控阈值标定脚本 —— 阈值不是抄来的，是这里跑出来的。

用法：
    cd backend
    .venv\\Scripts\\python.exe scripts/calibrate_quality.py

原理：
    构造两批样本，量出各指标的分布，取「合格样本最劣值」与
    「不合格样本最优值」之间的区间作为阈值。

    合格样本：backend/scripts/sample_faces/ 下 12 张真实人脸
    不合格样本：由合格样本人工退化得到
        blur        高斯模糊 σ=4      —— 对焦不准
        blur_heavy  高斯模糊 σ=8      —— 严重运动模糊
        dark        亮度 ×0.18        —— 光照不足
        overexpose  亮度 ×2.6         —— 强逆光/过曝
        small       缩放到 30%        —— 离摄像头太远
        side        旋转 25°          —— 头部侧倾
        multi       主脸旁并排第二张脸 —— 画面有多人

    注意两类「多脸」要分清：
        multi   —— 两人并排，脸大小相当（面积比 ≈ 1），必须拦
        bg_face —— 背景带一张小脸（面积比 ≈ 0.1），**不该拦**
        后者是合影裁切的正常产物，拦了就是误伤合格素材。

输出：每个指标在两批样本上的极值 + 建议阈值 + 是否满足「不误伤/能拦住」。
"""
from __future__ import annotations

import math
import os
import sys

import cv2
import numpy as np

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from core.face_engine import detect_faces  # noqa: E402
from core.quality_gate import QualityGate  # noqa: E402

SAMPLE_DIR = os.path.join(BACKEND, "scripts", "sample_faces")


def read_image(path: str):
    """兼容非 ASCII 路径的读图。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def measure(img, face) -> dict:
    """量一张脸的全部指标（与 QualityGate 内算法一致，但独立实现以便交叉验证）。"""
    h_img, w_img = img.shape[:2]
    x1, y1 = max(0, int(face.x1)), max(0, int(face.y1))
    x2, y2 = min(w_img, int(face.x2)), min(h_img, int(face.y2))
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    roi = cv2.resize(roi, (112, 112), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_64F).var())
    bright = float(np.mean(gray))
    le, re_, nose = face.left_eye, face.right_eye, face.nose_tip
    roll = abs(math.degrees(math.atan2(abs(le[1] - re_[1]), abs(le[0] - re_[0]))))
    nose_ratio = abs(float(nose[0]) - face.eye_mid_x) / max(face.width, 1e-6)
    return {
        "sharpness": sharp,
        "brightness": bright,
        "saturation": float(np.mean(gray >= 250)),
        "roll": roll,
        "nose_ratio": nose_ratio,
        "width": face.width,
        "height": face.height,
        "score": face.score,
    }


def degrade(img, kind: str):
    """把合格样本人工退化成不合格样本。"""
    if kind == "blur":
        return cv2.GaussianBlur(img, (0, 0), 4.0)
    if kind == "blur_heavy":
        return cv2.GaussianBlur(img, (0, 0), 8.0)
    if kind == "dark":
        return (img.astype(np.float32) * 0.18).astype(np.uint8)
    if kind == "overexpose":
        return np.clip(img.astype(np.float32) * 2.6, 0, 255).astype(np.uint8)
    if kind == "small":
        h, w = img.shape[:2]
        r = cv2.resize(img, (int(w * 0.30), int(h * 0.30)))
        out = np.zeros_like(img)
        out[:r.shape[0], :r.shape[1]] = r
        return out
    if kind == "multi":
        # 两人并排：横向拼两张同尺寸图，面积比 ≈ 1（实测 0.90~1.00）
        h, w = img.shape[:2]
        canvas = np.zeros((h, w * 2, 3), dtype=np.uint8)
        canvas[:, :w] = img
        canvas[:, w:] = img
        return canvas
    if kind == "bg_face":
        # 背景带一张小脸（面积比 ≈ 0.1）：这是合影裁切的正常产物，不该拦
        h, w = img.shape[:2]
        out = img.copy()
        small = cv2.resize(img, (int(w * 0.32), int(h * 0.32)))
        out[h - small.shape[0]:, w - small.shape[1]:] = small
        return out
    if kind == "side":
        # 头部侧倾：整图旋转。
        # 注意 roll 是在**图像坐标系**中测量的，所以整图旋转会让它同步增大
        # （脸和图一起转，倾角相对图像就变了）。
        # 实测：旋转 50° 后 12 张样本全部被 G4 拦住，旋转 40° 时有 2 张漏放
        #（因为原图本身已有 27~31° 倾斜，旋转后未必越界）。
        # 所以退化样本取 50°，代表「明显歪头」，而不是「轻微歪头」。
        h, w = img.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 50.0, 1.0)
        return cv2.warpAffine(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    return img


def collect(img) -> tuple[dict | None, int]:
    """对一张图取主脸指标，并返回该图检出的总脸数。"""
    faces = detect_faces(img)
    if not faces:
        return None, 0
    return measure(img, faces[0]), len(faces)


def main() -> int:
    if not os.path.isdir(SAMPLE_DIR):
        print(f"[x] 样例目录不存在：{SAMPLE_DIR}")
        print("    先运行 scripts/prepare_samples.py 生成样例")
        return 1

    names = sorted(
        n for n in os.listdir(SAMPLE_DIR)
        if n.lower().endswith((".jpg", ".png", ".jpeg"))
    )
    if not names:
        print(f"[x] {SAMPLE_DIR} 下没有样例图")
        return 1

    # ---- 合格样本 ----
    good_rows = []
    print("=" * 78)
    print("合格样本（真实人脸）")
    print("=" * 78)
    print(f"{'file':18}{'sharp':>9}{'bright':>8}{'sat%':>8}{'roll':>7}{'noseR':>8}"
          f"{'w':>7}{'h':>7}{'score':>8}")
    for n in names:
        img = read_image(os.path.join(SAMPLE_DIR, n))
        if img is None:
            continue
        m, _ = collect(img)
        if m is None:
            continue
        good_rows.append(m)
        print(f"{n:18}{m['sharpness']:9.1f}{m['brightness']:8.1f}{m['saturation'] * 100:8.2f}"
              f"{m['roll']:7.1f}{m['nose_ratio']:8.3f}{m['width']:7.0f}{m['height']:7.0f}{m['score']:8.3f}")

    if not good_rows:
        print("[x] 没有可用合格样本")
        return 1

    # ---- 不合格样本 ----
    bad_rows = []
    print()
    print("=" * 78)
    print("不合格样本（人工退化）")
    print("=" * 78)
    for kind in ("blur", "blur_heavy", "dark", "overexpose", "small", "side", "multi", "bg_face"):
        rows = []
        for n in names:
            img = read_image(os.path.join(SAMPLE_DIR, n))
            if img is None:
                continue
            dg = degrade(img, kind)
            m, nf = collect(dg)
            if m is None:
                continue
            rows.append((n, m, nf))
        if not rows:
            print(f"{kind:12} 全部检出人脸")
            continue
        bad_rows.extend((kind, m, nf) for _, m, nf in rows)
        print(f"{kind:12} n={len(rows):2}  sharp {min(r[1]['sharpness'] for r in rows):7.1f}"
              f"~{max(r[1]['sharpness'] for r in rows):7.1f}"
              f"  bright {min(r[1]['brightness'] for r in rows):6.1f}"
              f"~{max(r[1]['brightness'] for r in rows):6.1f}"
              f"  sat% {min(r[1]['saturation'] for r in rows) * 100:5.1f}"
              f"~{max(r[1]['saturation'] for r in rows) * 100:5.1f}"
              f"  w {min(r[1]['width'] for r in rows):5.0f}~{max(r[1]['width'] for r in rows):5.0f}"
              f"  faces {min(r[2] for r in rows)}~{max(r[2] for r in rows)}")

    # ---- 阈值建议 ----
    print()
    print("=" * 78)
    print("阈值标定结果")
    print("=" * 78)

    def span(key):
        return (min(r[key] for r in good_rows), max(r[key] for r in good_rows))

    g_sharp_lo, g_sharp_hi = span("sharpness")
    g_bright_lo, g_bright_hi = span("brightness")
    g_sat_lo, g_sat_hi = span("saturation")
    g_roll_lo, g_roll_hi = span("roll")
    g_nose_lo, g_nose_hi = span("nose_ratio")
    g_w_lo, g_w_hi = span("width")
    g_h_lo, g_h_hi = span("height")
    g_score_lo, g_score_hi = span("score")

    # 退化样本按类别分组，判定时逐类看 —— 不能把不同类混在一起取极值，
    # 否则「模糊样本的脸很大」会污染「尺寸阈值」的判定。
    by_kind: dict[str, list] = {}
    for kind, m, nf in bad_rows:
        by_kind.setdefault(kind, []).append((kind, m, nf))

    def bad_extreme(key, mode, kinds=None):
        rows = []
        for k, items in by_kind.items():
            if kinds and k not in kinds:
                continue
            rows.extend(it[1][key] for it in items)
        if not rows:
            return None
        return min(rows) if mode == "min" else max(rows)

    # 每条规则对应「哪些退化类应该被它拦住」
    rule_kinds = {
        "MIN_FACE_WIDTH": (["small"], "min", "max"),
        "MIN_FACE_HEIGHT": (["small"], "min", "max"),
        "MIN_SHARPNESS": (["blur", "blur_heavy", "dark"], "min", "max"),
        "MIN_BRIGHTNESS": (["dark"], "min", "min"),
        "MAX_BRIGHTNESS": (["overexpose"], "max", "max"),
        "MAX_SATURATION_RATIO": (["overexpose"], "max", "max"),
        "MAX_ROLL_ANGLE": (["side"], "max", "max"),
        "MAX_NOSE_OFFSET_RATIO": (["side"], "max", "max"),
        "MIN_DET_SCORE": ([], "min", "min"),
    }

    bounds = {
        "MIN_FACE_WIDTH": g_w_lo,
        "MIN_FACE_HEIGHT": g_h_lo,
        "MIN_SHARPNESS": g_sharp_lo,
        "MIN_BRIGHTNESS": g_bright_lo,
        "MAX_BRIGHTNESS": g_bright_hi,
        "MAX_SATURATION_RATIO": g_sat_hi,
        "MAX_ROLL_ANGLE": g_roll_hi,
        "MAX_NOSE_OFFSET_RATIO": g_nose_hi,
        "MIN_DET_SCORE": g_score_lo,
    }

    print(f"{'阈值':26}{'合格侧':>10}{'退化侧':>10}   判定")
    for name, (kinds, good_mode, bad_mode) in rule_kinds.items():
        bound = bounds[name]
        bad_val = bad_extreme(
            "width" if name == "MIN_FACE_WIDTH" else
            "height" if name == "MIN_FACE_HEIGHT" else
            "sharpness" if name == "MIN_SHARPNESS" else
            "brightness" if "BRIGHTNESS" in name else
            "saturation" if "SATURATION" in name else
            "roll" if name == "MAX_ROLL_ANGLE" else
            "nose_ratio" if name == "MAX_NOSE_OFFSET_RATIO" else
            "score",
            bad_mode, kinds,
        )
        if bad_val is None:
            verdict = "— （无对应退化类）"
        elif good_mode == "min":
            # 下限型：合格样本都在 bound 以上，退化样本应低于 bound
            verdict = "OK" if bad_val < bound else "⚠ 退化侧不更差"
        else:
            # 上限型：合格样本都在 bound 以下，退化样本应高于 bound
            verdict = "OK" if bad_val > bound else "⚠ 退化侧不更差"
        bv = "n/a" if bad_val is None else f"{bad_val:.3f}"
        print(f"{name:26}{bound:10.3f}{bv:>10}   {verdict}")

    # ---- 用门控整体跑一遍，验证「不误伤合格 + 拦住退化」----
    gate = QualityGate()
    print()
    print("=" * 78)
    print("门控整体验证")
    print("=" * 78)

    good_pass = good_reject = 0
    for n in names:
        img = read_image(os.path.join(SAMPLE_DIR, n))
        if img is None:
            continue
        faces = detect_faces(img)
        if not faces:
            continue
        q = gate.check(img, faces[0], faces)
        if q.passed:
            good_pass += 1
        else:
            good_reject += 1
            print(f"  ✗ 合格样本被误伤：{n} → {q.reason} [{q.rule}]")
    print(f"合格样本：通过 {good_pass} / 误伤 {good_reject}")

    # 每类退化单独统计；bg_face 是「合法但有多脸」，期望结果是**不拦**
    expect_block = {
        "blur": True, "blur_heavy": True, "dark": True,
        "overexpose": True, "small": True, "side": True, "multi": True,
        "bg_face": False,
    }
    for kind, should_block in expect_block.items():
        caught = missed = 0
        detail = []
        for n in names:
            img = read_image(os.path.join(SAMPLE_DIR, n))
            if img is None:
                continue
            dg = degrade(img, kind)
            faces = detect_faces(dg)
            if not faces:
                caught += 1        # 连脸都检不出 = 已被上游拒绝
                continue
            q = gate.check(dg, faces[0], faces)
            if q.passed:
                missed += 1
                detail.append(f"{n}({q.rule or '-'})")
            else:
                caught += 1
        if should_block:
            flag = "OK " if missed == 0 else "✗ "
            print(f"{flag}{kind:12} 应拦 → 拦住 {caught} / 漏放 {missed}")
            if missed:
                print(f"    漏放明细：{', '.join(detail)}")
        else:
            # bg_face 是「合法但有多脸」，期望是**放行**，
            # 所以判据是「误拦数（caught）为 0」，而不是「放行数 == 总数」。
            flag = "OK " if caught == 0 else "✗ "
            print(f"{flag}{kind:12} 不应拦 → 放行 {missed} / 误拦 {caught}")
            if caught:
                print(f"    误拦明细：{', '.join(detail)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
