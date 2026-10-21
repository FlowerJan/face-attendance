# -*- coding: utf-8 -*-
"""演示库 PII 守卫：截图 / 提交前检查库内是否残留「看起来像真的」的个人信息。

背景
----
本项目曾把带真实手机号的截图提交到**公开仓库**（docs/screenshots/01-students.png）。
.text 扫描全绿查不出来——因为泄露载体是 PNG 的像素，不是文本。
本脚本把检查前移到「数据层」：只要库里还是安全的假号，截出来的图就是安全的。

检查项
------
1. 手机号：允许 138000000xx 段的明显假号；其它号段一律视为风险。
2. 姓名字段：不做拦截（演示数据用生成姓名），但会统计供人工复核。
3. 人脸照片：确认 uploads/ 下的图片没有可识别的真实人脸文件名前缀。

用法（在 backend 目录下）：
    .venv/Scripts/python.exe scripts/pii_guard.py
    .venv/Scripts/python.exe scripts/pii_guard.py --allow-real   # 明确豁免（需写明理由）

退出码：0 = 通过；1 = 发现风险（此时禁止截图与提交）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# 允许的假号段：138000000xx
SAFE_PHONE = re.compile(r"^1380000\d{4}$")
# 中国大陆手机号形态
PHONE_LIKE = re.compile(r"^1[3-9]\d{9}$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-real", action="store_true",
                    help="显式豁免真实号段（需在 PR 描述里写明理由）")
    args = ap.parse_args()

    from core.db import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    bad, total, safe = [], 0, 0
    try:
        rows = db.execute(text("select student_no, name, phone from students")).all()
    except Exception as exc:  # noqa: BLE001
        print(f"[pii-guard] 读取 students 失败：{exc}")
        return 1

    for student_no, name, phone in rows:
        total += 1
        if phone is None or str(phone).strip() == "":
            continue
        p = str(phone).strip()
        if SAFE_PHONE.match(p):
            safe += 1
        elif PHONE_LIKE.match(p):
            bad.append((student_no, name, p))
        else:
            print(f"[pii-guard] 提示：{student_no} 的联系方式格式异常：{p!r}")

    print(f"[pii-guard] 学生总数 {total}，假号 {safe}，风险号 {len(bad)}")

    # 人脸图片文件名：真实人像常见中文长文件名，AI/演示素材是 ASCII 短名
    img_dir = BACKEND / "uploads" / "faces"
    suspicious_imgs = []
    if img_dir.exists():
        for p in img_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                if not p.name.isascii():
                    suspicious_imgs.append(str(p.relative_to(BACKEND)))

    ok = True
    if bad and not args.allow_real:
        print("\n[pii-guard] ✗ 发现疑似真实手机号，禁止截图与提交：")
        for student_no, name, p in bad:
            print(f"    {student_no}  {name}  {p}")
        print("    修复：把手机号统一改成 1380000xxxx 段假号，"
              "或执行 scripts/seed_demo.py --drop --split-identities 重建。")
        ok = False
    elif bad:
        print(f"[pii-guard] ⚠ --allow-real 已豁免 {len(bad)} 个真实号段，请写明理由。")

    if suspicious_imgs:
        print(f"\n[pii-guard] ⚠ 发现 {len(suspicious_imgs)} 个非 ASCII 文件名的图片，请人工确认：")
        for s in suspicious_imgs[:10]:
            print(f"    {s}")

    if ok:
        print("[pii-guard] ✓ 通过，可以截图与提交。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
