"""质量门控（Quality Gate）—— 录入口的第一道闸。

为什么需要这道门：
    录进去一张糊图 / 侧脸 / 过曝照，后续识别天天失败，
    而且失败时无法回溯原因（库里只存了一张 jpg，谁知道当时拍成什么样）。
    门控把脏数据挡在入口，而不是等到识别失败再回头猜。

本项目与参考实现（YuNet）的三处**实测差异**，均已按本地数据重新标定：

  1. 五点顺序不同
     YuNet  : [右眼, 左眼, 鼻尖, 右嘴角, 左嘴角]
     insightface(SCRFD/ArcFace): [左眼, 右眼, 鼻尖, 左嘴角, 右嘴角]
     直接照抄「左眼-右眼连线倾角」的公式会算出 150~180°，
     因为左右眼 x 序反了，atan2 落进第二象限。
     → 本实现统一用 abs(dx)/abs(dy) 构角，天然对镜像不敏感。

  2. det_score 分布不同，不可跨模型比较
     insightface 的 det_score 是 SCRFD 检测头的输出，
     本机 12 张真实人脸样本的实测区间是 0.61~0.92；
     同一批图加高斯模糊后 score 反而升到 0.65~0.80。
     → 说明它**不随画质单调变化**，不能当画质指标用，
       只能当「检测器是否认得出这张脸」的下限，本实现取 0.50。

  3. 多人判定不能用「脸数量」，要用「面积比」
     本机样本里 alpha__3 / alpha__4 背景带人脸（合影裁切残留），
     检测到 2 张脸，但第二张面积只有第一张的 8%~17%。
     若按「≥2 张脸就拒」，会误杀自己 12 张合格样例中的 2 张。
     → 改为「第二张面积 ≥ 主脸 30% 才判定为画面有多人」。

标定方法见 backend/scripts/calibrate_quality.py（阈值全部由实测得出，
不是抄来的常数）。校准样本：12 张真实人脸 + 5 类人工退化（模糊/重度模糊/
过暗/过曝/远距离小脸），阈值取「正常样本最劣值」与「退化样本最优值」之间。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from core.face_types import FaceCandidate


# ============================================================
# 阈值（全部来自 calibrate_quality.py 的实测，见模块 docstring）
# ============================================================
MIN_FACE_WIDTH = 85         # 正常样本最小 94.7px；30% 缩放后最大 79px
MIN_FACE_HEIGHT = 105       # 正常样本最小 121.5px；30% 缩放后最大 101px
MIN_SHARPNESS = 25.0        # 正常样本最小 36.3；σ=4 模糊后最大 14.9
MIN_BRIGHTNESS = 55.0       # 正常样本最小 96.4；压暗到 18% 后为 24.3
MAX_BRIGHTNESS = 210.0      # 正常样本最大 173.2；提亮 2.6 倍后为 233.3
MAX_SATURATION_RATIO = 0.25  # 人脸 ROI 中接近纯白(>=250)的像素占比上限
MAX_ROLL_ANGLE = 35.0       # 正常样本最大 30.8°（原图确有轻微倾斜）
MAX_NOSE_OFFSET_RATIO = 0.25  # 正常样本最大 0.216
MIN_DET_SCORE = 0.50        # 正常样本最小 0.612
MULTI_FACE_AREA_RATIO = 0.30   # 第二张脸面积 / 主脸面积 ≥ 该值判为多人


@dataclass
class QualityResult:
    """门控结果：通过则携带明细，不通过则带中文原因（可直接展示给用户）。"""

    passed: bool
    reason: str = ""
    rule: str = ""            # 触发的规则编号，如 "G2"
    sharpness: float = 0.0
    brightness: float = 0.0
    roll_angle: float = 0.0
    nose_offset: float = 0.0   # 鼻偏（像素）
    saturation: float = 0.0    # ROI 中接近纯白的像素占比（0~1）
    face_width: float = 0.0
    face_height: float = 0.0
    det_score: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def metrics(self) -> str:
        """一行指标明细，给 UI 展示。"""
        return (
            f"尺寸 {self.face_width:.0f}×{self.face_height:.0f}px · "
            f"亮度 {self.brightness:.0f} · 清晰度 {self.sharpness:.0f} · "
            f"倾角 {self.roll_angle:.1f}°"
        )

    @property
    def metrics_full(self) -> str:
        """完整指标（含鼻偏与置信度），给诊断面板展示。"""
        return (
            f"尺寸 {self.face_width:.0f}×{self.face_height:.0f}px · "
            f"亮度 {self.brightness:.0f} · 清晰度 {self.sharpness:.0f} · "
            f"倾角 {self.roll_angle:.1f}° · 鼻偏 {self.nose_offset:.1f}px · "
            f"过曝 {self.saturation * 100:.0f}%"
        )

    def to_dict(self) -> dict:
        """结构化输出，供 API 返回给前端。"""
        return {
            "passed": self.passed,
            "reason": self.reason,
            "rule": self.rule,
            "metrics": self.metrics_full,
            "sharpness": round(self.sharpness, 2),
            "brightness": round(self.brightness, 2),
            "roll_angle": round(self.roll_angle, 2),
            "nose_offset": round(self.nose_offset, 2),
            "saturation": round(self.saturation, 4),
            "face_width": round(self.face_width, 1),
            "face_height": round(self.face_height, 1),
            "det_score": round(self.det_score, 4),
            **self.extra,
        }


class QualityGate:
    """人脸质量门控规则集。

    检查顺序是刻意设计的 —— **亮度必须排在清晰度之前**。

    原因：光照不足会直接导致清晰度指标偏低，但它是更上游的原因。
    如果先报「画面模糊」，用户会去「稳住摄像头」，永远解决不了问题；
    先报「光照不足，请开灯」，用户换个环境就通过了。

    规则表：
        G1 尺寸      脸框宽/高下限
        G2 多人      第二张脸面积 ≥ 主脸 30%
        G3 置信度    检测置信度下限
        G4 侧倾      双眼连线倾角上限
        G5 姿态      鼻尖相对双眼中点的横向偏移上限
        G6 亮度      人脸 ROI 平均灰度上下限
        G7 清晰度    ROI 归一化后的拉普拉斯方差下限
    """

    def __init__(
        self,
        min_face_width: int = MIN_FACE_WIDTH,
        min_face_height: int = MIN_FACE_HEIGHT,
        min_sharpness: float = MIN_SHARPNESS,
        min_brightness: float = MIN_BRIGHTNESS,
        max_brightness: float = MAX_BRIGHTNESS,
        max_roll_angle: float = MAX_ROLL_ANGLE,
        max_nose_offset_ratio: float = MAX_NOSE_OFFSET_RATIO,
        max_saturation_ratio: float = MAX_SATURATION_RATIO,
        min_det_score: float = MIN_DET_SCORE,
        multi_face_area_ratio: float = MULTI_FACE_AREA_RATIO,
    ) -> None:
        self.min_face_width = min_face_width
        self.min_face_height = min_face_height
        self.min_sharpness = min_sharpness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.max_roll_angle = max_roll_angle
        self.max_nose_offset_ratio = max_nose_offset_ratio
        self.max_saturation_ratio = max_saturation_ratio
        self.min_det_score = min_det_score
        self.multi_face_area_ratio = multi_face_area_ratio

    # -----------------------------------------------------------------
    # 单项指标
    # -----------------------------------------------------------------
    @staticmethod
    def normalized_roi(image: np.ndarray, face: FaceCandidate) -> np.ndarray:
        """裁出人脸 ROI 并归一化到 112×112。

        归一化是必须的：拉普拉斯方差随图像尺寸变化，
        同样的五官边缘被拉伸到更多像素后梯度变缓、方差下降，
        固定阈值就会对大脸过严、对小脸过松。
        112×112 与 ArcFace 的输入尺寸一致，方差才趋于稳定。
        """
        h_img, w_img = image.shape[:2]
        x1 = max(0, int(face.x1))
        y1 = max(0, int(face.y1))
        x2 = min(w_img, int(face.x2))
        y2 = min(h_img, int(face.y2))
        roi = image[y1:y2, x1:x2]
        if roi.size == 0:
            return roi
        return cv2.resize(roi, (112, 112), interpolation=cv2.INTER_AREA)

    @classmethod
    def brightness(cls, image: np.ndarray, face: FaceCandidate) -> float:
        """人脸 ROI 平均灰度（0~255）。

        用途：把「光照不足」和「真的模糊」区分开。
        两者现象都是清晰度低，但处置方式完全相反：
            光照不足 → 提增益/降噪会抹平细节 → 必须开灯
            对焦不准 → 边缘本身就不锐利     → 稳住 / 擦镜头
        """
        roi = cls.normalized_roi(image, face)
        if roi.size == 0:
            return 0.0
        return float(np.mean(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)))

    @classmethod
    def sharpness(cls, image: np.ndarray, face: FaceCandidate) -> float:
        """归一化 ROI 的拉普拉斯方差（越大越清晰）。

        实测参考（112×112 归一化后）：
            真实样本 36~172 ｜ σ=4 模糊后 3~15 ｜ 远距离小脸 46~128
        注意远距离小脸的清晰度并不低（像素少但边缘还在），
        所以「太远」必须靠尺寸规则拦，不能靠清晰度拦。
        """
        roi = cls.normalized_roi(image, face)
        if roi.size == 0:
            return 0.0
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)   # 抑制传感器噪声干扰高频
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @classmethod
    def saturation_ratio(cls, image: np.ndarray, face: FaceCandidate) -> float:
        """人脸 ROI 中接近纯白（灰度 ≥ 250）的像素占比（0~1）。

        为什么不用亮度均值判过曝：
            实测同一批图提亮 2.6 倍后，ROI 平均灰度只从 96 升到 193 ——
            看着「有点亮」，但实际上大量细节已经烧成纯白，不可逆地丢失了。
            而饱和像素占比从 0% 跳到 40%，区分度是均值的三倍以上。
            烧白是不可逆的（信息真没了），均值升高却可能仍保留细节，
            所以过曝必须用饱和度判，不能用亮度判。

        实测参考：
            正常样本 0.0%~5.3% ｜ 过曝样本 40.3%~89.7%
        """
        roi = cls.normalized_roi(image, face)
        if roi.size == 0:
            return 0.0
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray >= 250))

    @staticmethod
    def roll_angle(face: FaceCandidate) -> float:
        """双眼连线与水平线的夹角（度），0~90。

        用 abs(dx)/abs(dy) 构造角度，而不是直接 atan2(dy, dx)：
        - 直接 atan2 在左右眼 x 序反转时会落进第二象限（150~180°）；
        - 取绝对值后对左右顺序不敏感，
          同时对**镜像画面天然免疫**（前端摄像头就是镜像的）。
        """
        le, re = face.left_eye, face.right_eye
        dx = abs(float(le[0] - re[0]))
        dy = abs(float(le[1] - re[1]))
        if dx < 1e-6:
            return 90.0
        return abs(math.degrees(math.atan2(dy, dx)))

    @staticmethod
    def nose_offset(face: FaceCandidate) -> float:
        """鼻尖相对双眼中点的横向偏移（像素）。"""
        return abs(float(face.nose_tip[0]) - face.eye_mid_x)

    # -----------------------------------------------------------------
    # 汇总检查
    # -----------------------------------------------------------------
    def check(
        self,
        image: np.ndarray,
        face: FaceCandidate,
        all_faces: list[FaceCandidate] | None = None,
    ) -> QualityResult:
        """对单张人脸执行全部检查。

        Args:
            image:     BGR 原图
            face:      待检查的主脸
            all_faces: 本帧检出的所有人脸（用于多人判定），可空

        Returns:
            QualityResult
        """
        sharp = self.sharpness(image, face)
        bright = self.brightness(image, face)
        saturation = self.saturation_ratio(image, face)
        roll = self.roll_angle(face)
        nose = self.nose_offset(face)
        width, height = face.width, face.height
        score = face.score
        # 本帧检出的总脸数：G2 提示与前端诊断面板都要用，
        # 少了它会出现「提示说有 2 张脸、面板却显示 1 张」的不一致。
        face_count = len(all_faces) if all_faces else 1

        def result(passed: bool, reason: str = "", rule: str = "", **extra) -> QualityResult:
            return QualityResult(
                passed=passed,
                reason=reason,
                rule=rule,
                sharpness=sharp,
                brightness=bright,
                roll_angle=roll,
                nose_offset=nose,
                saturation=saturation,
                face_width=width,
                face_height=height,
                det_score=score,
                extra={"face_count": face_count, **extra},
            )

        # ---- G1 尺寸 ----
        if width < self.min_face_width or height < self.min_face_height:
            return result(
                False,
                f"人脸太小（{width:.0f}×{height:.0f}px），请靠近摄像头",
                "G1",
            )

        # ---- G2 多人 ----
        multi = self._multi_face_info(face, all_faces)
        if multi:
            return result(False, multi, "G2")

        # ---- G3 置信度 ----
        if score < self.min_det_score:
            return result(
                False,
                f"检测置信度不足（{score:.2f}），请重试",
                "G3",
            )

        # ---- G4 侧倾 ----
        if roll > self.max_roll_angle:
            return result(
                False,
                f"检测到侧脸（倾斜 {roll:.0f}°），请正对摄像头",
                "G4",
            )

        # ---- G5 姿态歪斜 ----
        max_nose = self.max_nose_offset_ratio * width
        if nose > max_nose:
            return result(
                False,
                f"面部姿态异常（鼻偏 {nose:.0f}px），请摆正",
                "G5",
            )

        # ---- G6 亮度（必须排在清晰度之前）----
        if bright < self.min_brightness:
            return result(
                False,
                f"光照不足（亮度 {bright:.0f}，下限 {self.min_brightness:.0f}），"
                f"请开灯或靠近光源",
                "G6",
            )
        # 过曝用**饱和像素占比**判，而不是亮度均值。
        # 实测：同一批图提亮 2.6 倍后，均值只从 96 升到 193（看着不夸张），
        # 但饱和占比从 0% 跳到 40% —— 细节已经烧成纯白，不可逆丢失。
        if saturation > self.max_saturation_ratio:
            return result(
                False,
                f"画面过曝（高光区域占比 {saturation * 100:.0f}%，"
                f"上限 {self.max_saturation_ratio * 100:.0f}%），请背对光源",
                "G6",
            )
        if bright > self.max_brightness:
            return result(
                False,
                f"画面过曝 / 强逆光（亮度 {bright:.0f}，上限 {self.max_brightness:.0f}），"
                f"请背对光源",
                "G6",
            )

        # ---- G7 清晰度（放最后：前面失败时先报更可执行的问题）----
        if sharp < self.min_sharpness:
            return result(
                False,
                f"画面模糊（清晰度 {sharp:.0f}），请稳住摄像头或擦净镜头",
                "G7",
            )

        return result(True)

    def _multi_face_info(
        self,
        face: FaceCandidate,
        all_faces: list[FaceCandidate] | None,
    ) -> str:
        """多人判定：第二张脸面积 ≥ 主脸一定比例才判为「画面有多人」。

        不能按「脸数量 ≥ 2」判 —— 本机样本中有背景带人脸的合影裁切，
        会误杀合格样例。返回空串表示通过。
        """
        if not all_faces or len(all_faces) < 2:
            return ""
        others = [f for f in all_faces if f is not face]
        if not others:
            return ""
        second = max(others, key=lambda f: f.area)
        ratio = second.area / max(face.area, 1e-6)
        if ratio >= self.multi_face_area_ratio:
            return f"画面中有 {len(all_faces)} 张人脸，请只保留一人"
        return ""
