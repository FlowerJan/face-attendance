"""Face repository — SQLite metadata + Milvus vector operations."""
import logging
import time
from sqlalchemy import select, func, join as sa_join
from sqlalchemy.orm import Session
from models.face import Face
from models.student import Student
from core.milvus import get_client
from core.config import settings
import numpy as np

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# 有效人脸索引（M5 硬门槛①）：1:N 检索只对"仍有效"的底片进行。
#
# 为什么需要它：
#   删除学生/删除单张脸时，三处存储（SQLite / Milvus / 磁盘）是分别删的，
#   一旦某一步失败就可能留下"Milvus 里还有向量、但 SQLite 已无记录"的孤儿向量，
#   此时若直接拿现场抓拍去全库搜索，仍可能命中孤儿向量 → "已注销学生还能打卡"。
#   参考项目 M5 把这条列为 HIGH 级失效模式（忘过滤 face_status → 已注销学生能打卡）。
#
# 做法：用 SQLite 的 faces 表 LEFT JOIN students(face_status=1) 作为唯一真相源，
#   维护一份进程内 face_id 集合；search_similar 的命中结果只保留在集合内的 face_id。
#   录入成功 / 删除时即时更新集合，应用启动时整体重建一次。
# 说明：不做 Milvus 层 filter 字段，避免改动已有集合 schema（会丢数据），
#   在召回结果上做二次过滤，对单请求一次检索而言开销可忽略。
# ────────────────────────────────────────────────────────────
_ACTIVE_FACE_IDS: set[int] = set()
_ACTIVE_BUILT = False


def refresh_active_index(db: Session) -> int:
    """从 SQLite 重建有效人脸集合：有底片且所属学生 face_status=1 的 face_id。"""
    global _ACTIVE_FACE_IDS, _ACTIVE_BUILT
    rows = db.execute(
        select(Face.face_id)
        .join(Student, Face.student_id == Student.id)
        .where(Student.face_status == 1)
    ).scalars().all()
    _ACTIVE_FACE_IDS = set(int(r) for r in rows)
    _ACTIVE_BUILT = True
    return len(_ACTIVE_FACE_IDS)


def add_active(face_id: int) -> None:
    _ACTIVE_FACE_IDS.add(int(face_id))


def remove_active(face_ids: list[int]) -> None:
    for fid in face_ids:
        _ACTIVE_FACE_IDS.discard(int(fid))


def _is_active(face_id: int) -> bool:
    return int(face_id) in _ACTIVE_FACE_IDS


# ---- SQLite operations ----

def create_face_record(db: Session, student_id: int, image_path: str) -> Face:
    """Create face metadata record. face_id assigned after Milvus insert."""
    face = Face(face_id=0, student_id=student_id, image_path=image_path)
    db.add(face)
    db.commit()
    db.refresh(face)
    return face


def get_faces_by_student(db: Session, student_id: int) -> list[Face]:
    return db.execute(
        select(Face).where(Face.student_id == student_id).order_by(Face.created_at.desc())
    ).scalars().all()


def get_face_by_id(db: Session, face_id: int) -> Face | None:
    return db.execute(
        select(Face).where(Face.face_id == face_id)
    ).scalar_one_or_none()


def get_all_face_ids_by_student(db: Session, student_id: int) -> list[int]:
    rows = db.execute(
        select(Face.face_id).where(Face.student_id == student_id)
    ).scalars().all()
    return list(rows)


def delete_face_record(db: Session, face_id: int) -> bool:
    face = db.execute(select(Face).where(Face.face_id == face_id)).scalar_one_or_none()
    if not face:
        return False
    db.delete(face)
    db.commit()
    return True


def count_faces_by_student(db: Session, student_id: int) -> int:
    return db.execute(
        select(func.count(Face.id)).where(Face.student_id == student_id)
    ).scalar()


def get_all_faces(db: Session) -> list[Face]:
    return db.execute(select(Face)).scalars().all()


# ---- Milvus operations ----

def insert_vector(face_id: int, student_id: int, embedding: np.ndarray):
    c = get_client()
    c.insert(
        collection_name=settings.COLLECTION_NAME,
        data=[{
            "face_id": face_id,
            "student_id": student_id,
            "embedding": embedding.tolist(),
            "created_at": int(time.time()),
        }],
    )


def delete_vectors(face_ids: list[int]):
    c = get_client()
    if face_ids:
        c.delete(
            collection_name=settings.COLLECTION_NAME,
            filter=f"face_id in {face_ids}",
        )


def search_similar(embedding: np.ndarray, top_k: int = 5, only_active: bool = True,
                   exclude_student_id: int | None = None) -> list[dict]:
    """1:N 向量检索。

    exclude_student_id:
        在 Milvus 层排除某个学号的全部向量后检索。用于「在库里找另一个人」——
        典型场景是签到识别要算识别余量（top1 与「另一个人」最高相似度的差）。
        为什么必须在 Milvus 层排除而不是取回后再筛：
        同一学生常录多张底片，若先取 top_k 再在结果里找异人，
        窗口会被他自己的底片占满（top_k=3 时前 3 名可能全是同一人），
        导致「另一个人」根本不在窗口里 → 余量恒为 None → 歧义检测静默失效。
        参考实现即采用"排除 top1 所属人后再检索"的等价做法。
    """
    c = get_client()
    search_kwargs = dict(
        collection_name=settings.COLLECTION_NAME,
        data=[embedding.tolist()],
        limit=top_k,
        output_fields=["face_id", "student_id", "created_at"],
        search_params={"metric_type": "COSINE"},
    )
    if exclude_student_id is not None:
        search_kwargs["filter"] = f"student_id != {int(exclude_student_id)}"
    results = c.search(**search_kwargs)
    hits = []
    if results and results[0]:
        for hit in results[0]:
            fid = hit.entity.get("face_id")
            if only_active and not _is_active(fid):
                continue  # M5 硬门槛①：已失效 / 孤儿向量不参与匹配
            hits.append({
                "face_id": fid,
                "student_id": hit.entity.get("student_id"),
                "similarity": float(hit.distance),
            })
    return hits


def get_all_vectors() -> list[dict]:
    """Pull all vectors for offline clustering (ML audit)."""
    c = get_client()
    results = c.query(
        collection_name=settings.COLLECTION_NAME,
        filter="",
        output_fields=["face_id", "student_id", "embedding", "created_at"],
        limit=16384,
    )
    return results if results else []
