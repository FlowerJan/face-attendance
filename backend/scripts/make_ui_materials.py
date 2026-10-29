# -*- coding: utf-8 -*-
"""生成 UI 跑批素材（真实人脸）到 docs/ui-run-materials/。

为什么要有这个脚本
----------------
UI 全流程跑批（见 docs/M0-M6-UI跑批指南.md）需要几类素材：

| 类别 | 用途 |
|---|---|
| `alpha_enrolled_*.jpg` | 库内本人的原图 → 验证「认得出」 |
| `alpha_new_shot.jpg` | **同一人换了一张**（缩放+调光+重编码）→ 验证阈值不是靠「同一张图」蒙混 |
| `stranger_1..8.jpg` | **不同的人** → 验证「认不出」 |

这些是**真实人脸**，所以：
- 生成结果目录 `docs/ui-run-materials/` 已加入 `.gitignore`，**不入公开仓库**
  （本仓库此前已因截图泄露真实 PII 踩过坑）
- 但**生成方法**入仓库 —— 换机器、换素材后照样能复现

素材来源
--------
1. 本人 6 张：`backend/uploads/faces/20240001/`（M3 播种生成）
   —— 需先跑过播种，否则这里为空
2. 办公室远景（含多个不同的人，用于生成 stranger_*.jpg）：
   一张**多人合影**或办公室远景照片。用下面任一方式指定：

   - 命令行：``--panorama "D:\\photos\\office.png"``
   - 或指定所在目录（脚本按文件名关键字自动挑）：``--panorama-dir "D:\\photos"``
   - 或环境变量：``set UI_MATERIALS_PANORAMA=D:\\photos\\office.png``

   找不到时会跳过陌生人素材并明确提示（不影响其它素材）。

跑法
----
    cd backend
    .venv\Scripts\python.exe scripts\make_ui_materials.py [--panorama <文件> | --panorama-dir <目录>]

生成后用 `scripts/verify_ui_materials.py` 核对每张图的实际行为。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "ui-run-materials"

# 多人远景原图：一张照片里含多个不同的人，用于生成 stranger_*.jpg。
# 源图是**用户自己的素材**，本仓库不持有、也不该硬编码任何本机路径 ——
# 由命令行参数 / 环境变量传入。
PANORAMA_ENV = "UI_MATERIALS_PANORAMA"
PANORAMA_NAME_KEYS = ("全景", "远景", "panorama", "office")

# 陌生人脸框的最小边长（像素）：太小的人脸即使放大也过不了画质门控，
# 裁出来没意义，直接跳过。
MIN_FACE_SIDE = 28

ALPHA_DIR = BACKEND / "uploads" / "faces" / "20240001"


def find_panorama(explicit: str | None, src_dir: str | None) -> Path | None:
    """定位多人远景原图。优先级：命令行文件 > 环境变量 > 命令行目录 > 环境变量目录。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    env = os.environ.get(PANORAMA_ENV)
    if env:
        p = Path(env)
        if p.is_file():
            return p
        if p.is_dir():
            src_dir = src_dir or env

    if src_dir:
        d = Path(src_dir)
        if not d.is_dir():
            return None
        cands = sorted(
            p for p in d.iterdir()
            if p.is_file()
            and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and any(k in p.name for k in PANORAMA_NAME_KEYS)
        )
        return cands[0] if cands else None
    return None


def make_alpha_new_shot(src: Path, dst: Path) -> None:
    """同一人换一张照片：缩放 90% + 轻微调光 + 重新 JPEG 编码（质量 88）。

    为什么要「换一张」而不是直接复用原图：
    直接传同一张图，相似度恒为 1.0，**证明不了阈值是有效的** ——
    那样任何阈值都能「认出」。换一张之后相似度掉到 0.97 左右，
    才能证明 0.56 这条线真的区分得开。
    """
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"解码失败：{src}")
    h, w = img.shape[:2]
    small = cv2.resize(img, (int(w * 0.9), int(h * 0.9)), interpolation=cv2.INTER_AREA)
    crop = small[20:small.shape[0] - 20, 30:small.shape[1] - 30]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= 0.92                      # 略降饱和
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.06, 0, 255)   # 略提亮
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    buf.tofile(str(dst))


def make_strangers(pano: Path) -> list[Path]:
    """从办公室远景裁出 N 个不同的人。

    ★ 脸框**不硬编码** —— 现场跑一次检测，按面积从大到小取。
    硬编码框的问题是：换了源图编号就会错位，导致指南里写的
    「stranger_1 是谁」对不上号（实测踩过：同一张脸两次跑出不同编号）。
    """
    import cv2
    import numpy as np
    from insightface.app import FaceAnalysis

    img = cv2.imdecode(np.fromfile(str(pano), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"解码失败：{pano}")

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    faces = app.get(img)

    # 按面积从大到小排序 —— 编号越小脸越大、最容易过质检
    boxes = []
    for fc in faces:
        x1, y1, x2, y2 = fc.bbox.astype(int)
        if max(x2 - x1, y2 - y1) < MIN_FACE_SIDE:
            continue
        boxes.append((x1, y1, x2, y2))
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)

    made: list[Path] = []
    for i, (x1, y1, x2, y2) in enumerate(boxes, 1):
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        r = max(x2 - x1, y2 - y1) * 3 // 2      # 向外扩一圈，保留上下文
        x1n, y1n = max(0, cx - r), max(0, cy - r)
        x2n, y2n = min(img.shape[1], cx + r), min(img.shape[0], cy + r)
        crop = img[y1n:y2n, x1n:x2n]
        crop = cv2.resize(crop, (320, 320), interpolation=cv2.INTER_CUBIC)
        p = OUT / f"stranger_{i}.jpg"
        ok, buf = cv2.imencode(".jpg", crop)
        if not ok:
            continue
        buf.tofile(str(p))
        made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 UI 跑批素材（真实人脸）")
    ap.add_argument("--panorama", help="多人远景原图路径（图片里含多个不同的人）")
    ap.add_argument("--panorama-dir", help="多人远景原图所在目录（按文件名关键字自动挑选）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{OUT}")

    # 1) 库内本人的原图
    if not ALPHA_DIR.is_dir():
        print(f"\n[FAIL] 找不到 {ALPHA_DIR}")
        print("       需要先播种演示数据：")
        print("       .venv\\Scripts\\python.exe scripts\\seed_demo.py --drop --students 28 \\")
        print("           --faces-from scripts/sample_faces --max-enroll 20")
        return 1
    srcs = sorted(ALPHA_DIR.glob("*.jpg"))
    if not srcs:
        print(f"\n[FAIL] {ALPHA_DIR} 里没有底片")
        return 1
    for f in srcs:
        dst = OUT / f"alpha_enrolled_{f.name}"
        dst.write_bytes(f.read_bytes())
    print(f"[OK] 本人原图 {len(srcs)} 张 → alpha_enrolled_*.jpg")

    # 2) 同一人换一张
    try:
        make_alpha_new_shot(srcs[-1], OUT / "alpha_new_shot.jpg")
        print("[OK] 同一人新照片 → alpha_new_shot.jpg（缩放 90% + 调光 + 重编码）")
    except Exception as e:
        print(f"[FAIL] 生成 alpha_new_shot.jpg 失败：{e}")
        return 1

    # 3) 陌生人
    pano = find_panorama(args.panorama, args.panorama_dir)
    if pano is None:
        print("\n[SKIP] 未找到多人远景原图，跳过陌生人素材")
        print("       用 --panorama <文件> / --panorama-dir <目录> 指定，")
        print(f"       或设环境变量 {PANORAMA_ENV}。")
        print("       （stranger_*.jpg 用于验证「认不出」；没有它签到页只能测成功路径）")
    else:
        made = make_strangers(pano)
        print(f"[OK] 陌生人 {len(made)} 张 → stranger_*.jpg（源：{pano.name[:40]}）")

    print(f"\n完成。共 {len(list(OUT.glob('*.jpg')))} 个素材文件。")
    print("接着可以核对每张图的实际行为：")
    print("  .venv\\Scripts\\python.exe scripts\\verify_ui_materials.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
