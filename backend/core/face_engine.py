"""insightface singleton — model loaded once at startup, resident in memory."""
import numpy as np
from insightface.app import FaceAnalysis
from core.config import settings

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


def detect_and_embed(image_bgr: np.ndarray) -> dict | None:
    """
    Detect the largest face in image, return embedding + landmarks.

    Returns None if no face found.
    """
    app = get_face_app()
    faces = app.get(image_bgr)

    if not faces:
        return None

    # quality control: only keep the largest face (by bbox area)
    largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    return {
        "embedding": largest.normed_embedding,   # 512-dim, already L2-normalized
        "bbox": largest.bbox.tolist(),
        "landmark_5": largest.kps.tolist() if hasattr(largest, "kps") else None,
        "det_score": float(largest.det_score),
    }
