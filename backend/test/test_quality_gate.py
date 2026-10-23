# -*- coding: utf-8 -*-
"""质量门控单元测试。

设计要点：
  1. **不加载模型**。门控只依赖 FaceCandidate 这层解耦结构，
     所以测试可以手工构造关键点，跑得飞快，也不占 300MB 内存。
  2. **每条规则一个用例**，失败时能立刻看出是哪条规则坏了。
  3. **镜像不变性**要单独断言 —— 前端摄像头是镜像的，
     左右眼 x 序会反转，这个 bug 在真实使用中才会暴露。

跑法：
    cd backend
    .venv\\Scripts\\python.exe -m pytest test/test_quality_gate.py -v   # pytest 收集
    .venv\\Scripts\\python.exe test/test_quality_gate.py                # 手动模式（逐项打印）
"""
import os
import sys

import numpy as np

# backend/ 注入 sys.path，让 core.* 可解析
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.face_types import FaceCandidate
from core.quality_gate import QualityGate, MIN_FACE_WIDTH  # noqa: E402


def make_face(
    bbox=(100.0, 100.0, 300.0, 300.0),
    kps=None,
    score=0.9,
) -> FaceCandidate:
    """构造一张"标准合格"的人脸。

    默认关键点基于 bbox 中心对称摆放：
        左眼 (150,170) / 右眼 (250,170) / 鼻尖 (200,210)
    这样 roll=0°、鼻偏=0，是所有用例的干净基线。
    """
    if kps is None:
        kps = np.array([
            [150.0, 170.0],   # 左眼
            [250.0, 170.0],   # 右眼
            [200.0, 210.0],   # 鼻尖
            [170.0, 260.0],   # 左嘴角
            [230.0, 260.0],   # 右嘴角
        ])
    return FaceCandidate(bbox=bbox, kps=np.asarray(kps, dtype=np.float64), score=score)


def make_image(brightness=140, size=400) -> np.ndarray:
    """构造一张可控亮度、带纹理的图像。

    纹理用棋盘格，否则纯色图算不出拉普拉斯方差（清晰度恒为 0）。
    """
    img = np.full((size, size, 3), brightness, dtype=np.uint8)
    checker = np.indices((size, size)).sum(axis=0) % 8 < 4
    img[checker] = np.clip(img[checker].astype(int) - 60, 0, 255)
    return img


# ============================================================
# 断言辅助
# ============================================================
class Checker:
    """极简断言收集器 —— 风格对齐项目里 test_student_repo.py。"""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool, detail: str = ""):
        if condition:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed += 1
            print(f"  FAIL  {label}  {detail}")


def run_tests() -> Checker:
    c = Checker()
    gate = QualityGate()

    print("\n[1] 合格样本应通过")
    img = make_image()
    face = make_face()
    q = gate.check(img, face, [face])
    c.check("标准人脸通过门控", q.passed, q.reason)

    print("\n[2] G1 尺寸：脸太小应被拒")
    small = make_face(bbox=(100.0, 100.0, 100.0 + MIN_FACE_WIDTH - 10, 100.0 + MIN_FACE_WIDTH - 10))
    q = gate.check(img, small, [small])
    c.check("过小脸被拒且规则为 G1", (not q.passed) and q.rule == "G1", q.reason)

    print("\n[3] G2 多人：第二张脸面积够大应被拒")
    big = make_face(bbox=(100.0, 100.0, 300.0, 300.0))
    other = make_face(bbox=(320.0, 100.0, 520.0, 300.0))
    q = gate.check(img, big, [big, other])
    c.check("并列双人被拒且规则为 G2", (not q.passed) and q.rule == "G2", q.reason)
    c.check(
        "多人时 face_count 反映本帧总脸数",
        q.to_dict().get("face_count") == 2,
        f"face_count={q.to_dict().get('face_count')}",
    )

    print("\n[4] G2 多人：背景小脸（面积比 < 30%）不应误伤")
    bg = make_face(bbox=(320.0, 100.0, 380.0, 160.0))   # 面积约为主脸 11%
    q = gate.check(img, big, [big, bg])
    c.check("背景小脸不触发多人拦截", q.passed, q.reason)
    c.check(
        "背景小脸存在时 face_count 仍为 2",
        q.to_dict().get("face_count") == 2,
        f"face_count={q.to_dict().get('face_count')}",
    )

    print("\n[5] G3 置信度：低分应被拒")
    low = make_face(score=0.30)
    q = gate.check(img, low, [low])
    c.check("低置信度被拒且规则为 G3", (not q.passed) and q.rule == "G3", q.reason)

    print("\n[6] G4 侧倾：眼睛连线倾斜应被拒")
    # 左眼(150,120) 右眼(250,220) → 倾斜 45°
    tilted_kps = np.array([
        [150.0, 120.0], [250.0, 220.0], [200.0, 210.0],
        [170.0, 260.0], [230.0, 260.0],
    ])
    tilted = make_face(kps=tilted_kps)
    q = gate.check(img, tilted, [tilted])
    c.check("侧倾 45° 被拒且规则为 G4", (not q.passed) and q.rule == "G4", q.reason)

    print("\n[7] G5 姿态：鼻尖明显偏离中点应被拒")
    skew_kps = np.array([
        [150.0, 170.0], [250.0, 170.0], [290.0, 210.0],   # 鼻尖偏 40px
        [170.0, 260.0], [230.0, 260.0],
    ])
    skew = make_face(kps=skew_kps)
    q = gate.check(img, skew, [skew])
    c.check("鼻偏过大被拒且规则为 G5", (not q.passed) and q.rule == "G5", q.reason)

    print("\n[8] G6 亮度：过暗应报『光照不足』而不是『画面模糊』")
    dark_img = make_image(brightness=20)
    q = gate.check(dark_img, face, [face])
    c.check("过暗被拒且规则为 G6", (not q.passed) and q.rule == "G6", q.reason)
    c.check("过暗提示是『光照不足』而非『模糊』", "光照不足" in q.reason, q.reason)

    print("\n[9] G6 亮度：过曝应被拒")
    bright_img = make_image(brightness=250)
    q = gate.check(bright_img, face, [face])
    c.check("过曝被拒且规则为 G6", (not q.passed) and q.rule == "G6", q.reason)

    print("\n[9b] G6 过曝：用饱和像素占比判，而非亮度均值")
    # 构造一张「均值不算太低、但大面积接近纯白」的图 ——
    # 模拟细节被烧成纯白（高光溢出）的情况。
    # 注意不能靠挖黑洞来造对比：那会把 ROI 均值拉到 0，
    # 先触发「光照不足」，根本走不到过曝规则。
    blown = np.full((400, 400, 3), 235, dtype=np.uint8)
    blown[::6, :] = 255          # 3/6 的像素烧成纯白
    blown[:, ::6] = 255
    q = gate.check(blown, face, [face])
    c.check("大面积纯白被过曝规则拦住", (not q.passed) and q.rule == "G6", q.reason)
    c.check("过曝提示提到高光占比", "过曝" in q.reason, q.reason)

    print("\n[10] G7 清晰度：纯色图（无纹理）应报画面模糊")
    flat = np.full((400, 400, 3), 140, dtype=np.uint8)
    q = gate.check(flat, face, [face])
    c.check("无纹理图被拒且规则为 G7", (not q.passed) and q.rule == "G7", q.reason)

    print("\n[11] 镜像不变性：左右眼 x 序反转，倾角判定不变")
    mirror_kps = np.array([
        [250.0, 170.0],   # 交换左右眼
        [150.0, 170.0],
        [200.0, 210.0],
        [230.0, 260.0],
        [170.0, 260.0],
    ])
    mirrored = make_face(kps=mirror_kps)
    q_mirror = gate.check(img, mirrored, [mirrored])
    c.check("镜像后仍判定为通过", q_mirror.passed, q_mirror.reason)
    c.check(
        "镜像前后倾角一致",
        abs(q_mirror.roll_angle - 0.0) < 1e-6,
        f"roll={q_mirror.roll_angle}",
    )

    print("\n[12] 亮度优先于清晰度（顺序不变量）")
    # 一张又暗又糊的图：必须先报 G6 亮度，而不是 G7 清晰度
    dark_flat = np.full((400, 400, 3), 20, dtype=np.uint8)
    q = gate.check(dark_flat, face, [face])
    c.check(
        "又暗又糊时优先报 G6（可执行性优先）",
        q.rule == "G6",
        f"rule={q.rule} reason={q.reason}",
    )

    print("\n[13] ROI 归一化：大脸小脸清晰度可比")
    from core.quality_gate import QualityGate as QG
    small_face = make_face(bbox=(150.0, 150.0, 190.0, 190.0))
    big_face = make_face(bbox=(50.0, 50.0, 350.0, 350.0))
    s_small = QG.sharpness(img, small_face)
    s_big = QG.sharpness(img, big_face)
    c.check(
        "归一化后清晰度量级接近（差 < 3 倍）",
        max(s_small, s_big) / max(min(s_small, s_big), 1e-6) < 3.0,
        f"small={s_small:.1f} big={s_big:.1f}",
    )

    return c


def test_quality_gate_rules():
    """pytest 入口：run_tests() 逐项断言，失败时输出里能看到具体 FAIL 行。"""
    c = run_tests()
    assert c.failed == 0, f"质量门控有 {c.failed} 项断言失败（详见上方 FAIL 行）"


def main() -> int:
    print("=" * 60)
    print("质量门控单元测试")
    print("=" * 60)
    c = run_tests()
    print()
    print("=" * 60)
    print(f"结果：通过 {c.passed} 项，失败 {c.failed} 项")
    print("=" * 60)
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
