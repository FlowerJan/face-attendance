"""Face candidate — 与检测后端解耦的人脸结构。

为什么单独定义一个 dataclass：
    质量门控（core/quality_gate.py）需要「框 + 五点 + 置信度」，
    但不应该直接依赖 insightface 的 Face 对象。
    原因有两个：
      1. insightface 的 Face 有几十个属性，门控只用到 3 个；
      2. 门控要能被单元测试直接喂数据，不需要加载 300MB 模型。
    所以在 face_engine 层做一次转换，向上只暴露最小必要结构。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FaceCandidate:
    """单张人脸的检测结果（与具体检测器无关）。

    坐标约定全部为原图像素坐标：
        bbox = (x1, y1, x2, y2)   左上角 + 右下角
        kps  = (5, 2)             5 个关键点，顺序见下
    """

    bbox: tuple[float, float, float, float]
    kps: np.ndarray          # (5, 2)：左眼 / 右眼 / 鼻尖 / 左嘴角 / 右嘴角
    score: float             # 检测置信度
    embedding: np.ndarray | None = None   # 512 维 L2 归一化特征（主脸才有）

    @property
    def x1(self) -> float:
        return float(self.bbox[0])

    @property
    def y1(self) -> float:
        return float(self.bbox[1])

    @property
    def x2(self) -> float:
        return float(self.bbox[2])

    @property
    def y2(self) -> float:
        return float(self.bbox[3])

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def left_eye(self) -> np.ndarray:
        """图像左侧的眼睛（关键点 0）。"""
        return self.kps[0]

    @property
    def right_eye(self) -> np.ndarray:
        """图像右侧的眼睛（关键点 1）。"""
        return self.kps[1]

    @property
    def nose_tip(self) -> np.ndarray:
        """鼻尖（关键点 2）。"""
        return self.kps[2]

    @property
    def eye_mid_x(self) -> float:
        """双眼横向中点 —— 鼻偏的参照基准。"""
        return (float(self.left_eye[0]) + float(self.right_eye[0])) / 2.0
