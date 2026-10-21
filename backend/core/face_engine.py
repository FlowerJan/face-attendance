"""insightface singleton — model loaded once at startup, resident in memory."""
import numpy as np
from insightface.app import FaceAnalysis
from core.config import settings
from core.face_types import FaceCandidate

_app: FaceAnalysis | None = None


def get_face_app() -> FaceAnalysis:
    global _app
    if _app is None:
        _app = FaceAnalysis(
            name=settings.FACE_MODEL_NAME,
            providers=["CPUExecutionProvider"],
        )
        _app.prepare(ctx_id=settings.FACE_CTX_ID, det_size=(640, 640))
    return _app


def status() -> dict:
    """Return face engine runtime status (used by /api/system/status)."""
    return {
        "model": settings.FACE_MODEL_NAME,
        "loaded": _app is not None,
        "providers": ["CPUExecutionProvider"],
    }


def detect_faces(image_bgr: np.ndarray) -> list[FaceCandidate]:
    """检测图像中的所有人脸，按面积从大到小排序。

    返回统一结构 FaceCandidate，而不是 insightface 的原始 Face，
    这样 core/quality_gate.py 不必依赖检测器实现。
    """
    app = get_face_app()
    faces = app.get(image_bgr)

    candidates = []
    for f in faces:
        kps = getattr(f, "kps", None)
        if kps is None:
            continue
        candidates.append(
            FaceCandidate(
                bbox=tuple(float(v) for v in f.bbox),
                kps=np.asarray(kps, dtype=np.float64),
                score=float(f.det_score),
                embedding=np.asarray(f.normed_embedding, dtype=np.float32),
            )
        )

    candidates.sort(key=lambda c: c.area, reverse=True)
    return candidates


def detect_and_embed(image_bgr: np.ndarray) -> dict | None:
    """
    Detect the largest face in image, return embedding + landmarks.

    Returns None if no face found.
    """
    candidates = detect_faces(image_bgr)
    if not candidates:
        return None

    largest = candidates[0]

    return {
        "embedding": largest.embedding,   # 512-dim, already L2-normalized
        "bbox": largest.bbox.tolist(),
        "landmark_5": largest.kps.tolist(),
        "det_score": largest.score,
    }
