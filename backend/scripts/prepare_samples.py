# -*- coding: utf-8 -*-
"""生成 e2e 测试用的真实人脸样本（无需联网、零额外成本）。

素材来源：
  1. insightface 包自带的 t1.jpg（多人合影）-> 可裁出多个不同身份
  2. 本机已有的同一人像多张图 -> 用于验证「重复注册」检测

输出：backend/scripts/sample_faces/<身份>__<第几张>.jpg
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BACKEND, "scripts", "sample_faces")
SITE = os.path.join(BACKEND, ".venv", "Lib", "site-packages", "insightface", "data", "images")

PROJ = r"E:\workbuddy-projects"

# (输出名, 源图路径, 取第几张脸按面积降序, 是否整体保留不裁)
SOURCES = [
    ("alpha__1.jpg", os.path.join(PROJ, "同一个戴黑框眼镜_穿白色polo衫的年轻男子_在同样的现代办_2026-09-18T03-14-27.png"), 0),
    ("alpha__2.jpg", os.path.join(PROJ, "同一个戴黑框眼镜_穿白色polo衫的年轻男子_在同样的现代办_2026-09-18T03-16-22.png"), 0),
    ("alpha__3.jpg", os.path.join(PROJ, "在这张照片中_保持男子和他的姿势_表情_眼镜_衣服完全不变__2026-09-18T02-59-10.png"), 0),
    ("bravo__1.jpg", os.path.join(SITE, "t1.jpg"), 0),
    ("charlie__1.jpg", os.path.join(SITE, "t1.jpg"), 1),
    ("delta__1.jpg", os.path.join(SITE, "t1.jpg"), 2),
    ("echo__1.jpg", os.path.join(SITE, "Tom_Hanks_54745.png"), 0),
]


def read_image(path: str):
    """兼容非 ASCII 路径的读图。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def write_image(path: str, img) -> bool:
    try:
        ext = os.path.splitext(path)[1]
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(path)
        return True
    except Exception:
        return False


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    sys.path.insert(0, BACKEND)
    from core.face_engine import get_face_app

    app = get_face_app()
    print("[model] buffalo_l loaded")

    made, skipped = [], []
    for name, src, rank in SOURCES:
        if not os.path.exists(src):
            skipped.append((name, "源文件不存在: " + src))
            continue
        img = read_image(src)
        if img is None:
            skipped.append((name, "读图失败"))
            continue

        faces = app.get(img)
        if not faces:
            skipped.append((name, "未检出人脸 (%s)" % os.path.basename(src)[:40]))
            continue

        faces = sorted(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        if rank >= len(faces):
            skipped.append((name, "只检出 %d 张脸，取不到第 %d 张" % (len(faces), rank + 1)))
            continue

        f = faces[rank]
        x1, y1, x2, y2 = [float(v) for v in f.bbox]
        w, h = x2 - x1, y2 - y1
        # 向外扩 60% 边距，模拟真实采集时的半身照
        mx, my = w * 0.6, h * 0.6
        X1 = max(0, int(x1 - mx))
        Y1 = max(0, int(y1 - my))
        X2 = min(img.shape[1], int(x2 + mx))
        Y2 = min(img.shape[0], int(y2 + my))
        crop = img[Y1:Y2, X1:X2].copy()

        # 统一缩放到宽 480，避免超大图
        if crop.shape[1] > 480:
            scale = 480 / crop.shape[1]
            crop = cv2.resize(crop, (480, int(crop.shape[0] * scale)))

        if write_image(os.path.join(OUT_DIR, name), crop):
            made.append((name, f.det_score, crop.shape, os.path.basename(src)[:36]))
        else:
            skipped.append((name, "写文件失败"))

    print("\n[生成成功] %d 张 -> %s" % (len(made), OUT_DIR))
    for name, score, shape, src in made:
        print("   %-15s det=%.3f  %dx%d  <- %s" % (name, score, shape[1], shape[0], src))
    if skipped:
        print("\n[跳过] %d 张" % len(skipped))
        for name, why in skipped:
            print("   %-15s %s" % (name, why))
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
