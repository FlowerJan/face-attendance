#!/usr/bin/env python3
"""Generate the complete FastAPI backend scaffold for face-attendance system."""
import os

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- directory structure ----
DIRS = [
    "core", "data", "models", "schemas",
    "repository", "service", "api",
]
for d in DIRS:
    os.makedirs(os.path.join(BASE, d), exist_ok=True)
    # __init__.py
    init_path = os.path.join(BASE, d, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w", encoding="utf-8") as f:
            f.write("")

FILES = {}

# ============================================================
# requirements.txt
# ============================================================
FILES["requirements.txt"] = """\
fastapi==0.136.0
uvicorn[standard]==0.30.6
sqlalchemy==2.0.36
pydantic==2.10.4
numpy==1.26.4
pandas==2.2.3
scikit-learn==1.6.1
insightface==1.0.1
onnxruntime==1.28.0
pymilvus==2.5.4
opencv-python==4.10.0.84
python-multipart==0.0.20
aiofiles==24.1.0
"""

# ============================================================
# core/config.py
# ============================================================
FILES["core/config.py"] = '''\
"""Application configuration (corresponds to SpringBoot application.yml)."""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ---- paths ----
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    FACE_IMG_DIR: Path = BASE_DIR / "uploads" / "faces"
    DATA_DIR: Path = BASE_DIR / "data"

    # ---- database ----
    SQLITE_URL: str = f"sqlite:///{BASE_DIR / 'attendance.db'}"

    # ---- milvus ----
    MILVUS_DB_PATH: str = str(BASE_DIR / "milvus_lite.db")
    COLLECTION_NAME: str = "face_vectors"
    EMBED_DIM: int = 512

    # ---- face engine ----
    FACE_MODEL_NAME: str = "buffalo_l"
    FACE_CTX_ID: int = -1   # -1 = CPU

    # ---- server ----
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()

# ensure upload dir exists
settings.FACE_IMG_DIR.mkdir(parents=True, exist_ok=True)
'''

# ============================================================
# core/db.py
# ============================================================
FILES["core/db.py"] = '''\
"""SQLAlchemy engine and session factory."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from core.config import settings

engine = create_engine(
    settings.SQLITE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables."""
    # import models so they register on Base.metadata
    from models import student, face, notice  # noqa: F401
    Base.metadata.create_all(bind=engine)
'''

# ============================================================
# core/milvus.py
# ============================================================
FILES["core/milvus.py"] = '''\
"""Milvus Lite client and collection management."""
from pymilvus import MilvusClient, DataType
from core.config import settings

_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(settings.MILVUS_DB_PATH)
        _ensure_collection()
    return _client


def _ensure_collection():
    c = get_client()
    if c.has_collection(settings.COLLECTION_NAME):
        return

    schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("face_id", DataType.INT64, is_primary=True)
    schema.add_field("student_id", DataType.INT64)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=settings.EMBED_DIM)
    schema.add_field("created_at", DataType.INT64)

    index_params = c.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    c.create_collection(
        collection_name=settings.COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )


def drop_collection():
    """Drop and recreate (for testing/reset)."""
    c = get_client()
    if c.has_collection(settings.COLLECTION_NAME):
        c.drop_collection(settings.COLLECTION_NAME)
    _ensure_collection()
'''

# ============================================================
# core/face_engine.py
# ============================================================
FILES["core/face_engine.py"] = '''\
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
'''

# ============================================================
# core/enums.py
# ============================================================
FILES["core/enums.py"] = '''\
"""CSV-driven enum container for classes and majors.

Loaded once at startup from data/*.csv files.
Modify enum = edit CSV + restart (no code change needed).
"""
import pandas as pd
from core.config import settings


class EnumContainer:
    _classes: list[str] = []
    _majors: list[str] = []

    @classmethod
    def load(cls):
        classes_csv = settings.DATA_DIR / "班级.csv"
        majors_csv = settings.DATA_DIR / "专业.csv"

        if classes_csv.exists():
            df = pd.read_csv(classes_csv, header=None, names=["name"])
            cls._classes = df["name"].dropna().tolist()

        if majors_csv.exists():
            df = pd.read_csv(majors_csv, header=None, names=["name"])
            cls._majors = df["name"].dropna().tolist()

    @classmethod
    def get_classes(cls) -> list[str]:
        return cls._classes

    @classmethod
    def get_majors(cls) -> list[str]:
        return cls._majors

    @classmethod
    def is_valid_class(cls, name: str) -> bool:
        return name in cls._classes

    @classmethod
    def is_valid_major(cls, name: str) -> bool:
        return name in cls._majors
'''

# ============================================================
# data/班级.csv
# ============================================================
FILES["data/班级.csv"] = "智造本2023-1班\n智造本2023-2班\n智造本2024-1班\n智造本2024-2班\n"

# ============================================================
# data/专业.csv
# ============================================================
FILES["data/专业.csv"] = "计算机科学与技术\n软件工程\n人工智能\n数据科学与大数据技术\n"

# ============================================================
# models/student.py
# ============================================================
FILES["models/student.py"] = '''\
"""Student ORM entity (corresponds to @Entity)."""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    class_name: Mapped[str | None] = mapped_column(String(50))
    major: Mapped[str | None] = mapped_column(String(50))
    enroll_year: Mapped[int | None] = mapped_column(Integer)
    phone: Mapped[str | None] = mapped_column(String(20))
    face_status: Mapped[int] = mapped_column(Integer, default=0)  # 0=not enrolled, 1=enrolled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
'''

# ============================================================
# models/face.py
# ============================================================
FILES["models/face.py"] = '''\
"""Face record ORM entity — bridges SQLite and Milvus via face_id."""
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base


class Face(Base):
    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    face_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
'''

# ============================================================
# models/notice.py
# ============================================================
FILES["models/notice.py"] = '''\
"""Notice (announcement) ORM entity."""
from datetime import datetime
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
'''

# ============================================================
# schemas/common.py
# ============================================================
FILES["schemas/common.py"] = '''\
"""Unified response wrapper."""
from typing import Any
from pydantic import BaseModel


class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Any = None


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def fail(message: str = "error", code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


class PageResult(BaseModel):
    total: int
    page: int
    page_size: int
    items: list
'''

# ============================================================
# schemas/student.py
# ============================================================
FILES["schemas/student.py"] = '''\
"""Student DTOs (corresponds to DTO/VO)."""
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from core.enums import EnumContainer


class StudentCreate(BaseModel):
    student_no: str = Field(..., max_length=20)
    name: str = Field(..., max_length=50)
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None

    @field_validator("class_name")
    @classmethod
    def validate_class(cls, v):
        if v and not EnumContainer.is_valid_class(v):
            raise ValueError(f"invalid class: {v}")
        return v

    @field_validator("major")
    @classmethod
    def validate_major(cls, v):
        if v and not EnumContainer.is_valid_major(v):
            raise ValueError(f"invalid major: {v}")
        return v


class StudentUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None


class StudentOut(BaseModel):
    id: int
    student_no: str
    name: str
    gender: str | None = None
    class_name: str | None = None
    major: str | None = None
    enroll_year: int | None = None
    phone: str | None = None
    face_status: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
'''

# ============================================================
# schemas/face.py
# ============================================================
FILES["schemas/face.py"] = '''\
"""Face DTOs."""
from datetime import datetime
from pydantic import BaseModel


class FaceOut(BaseModel):
    id: int
    face_id: int
    student_id: int
    image_path: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class FaceEnrollResult(BaseModel):
    detected: bool
    face_id: int | None = None
    image_path: str | None = None
    reason: str | None = None
'''

# ============================================================
# schemas/notice.py
# ============================================================
FILES["schemas/notice.py"] = '''\
"""Notice DTOs."""
from datetime import datetime
from pydantic import BaseModel


class NoticeCreate(BaseModel):
    title: str
    content: str | None = None
    publisher: str | None = None


class NoticeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    publisher: str | None = None


class NoticeOut(BaseModel):
    id: int
    title: str
    content: str | None = None
    publisher: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
'''

# ============================================================
# repository/student_repo.py
# ============================================================
FILES["repository/student_repo.py"] = '''\
"""Student repository — pure data access, no business logic."""
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from models.student import Student
from schemas.student import StudentCreate, StudentUpdate


def create(db: Session, data: StudentCreate) -> Student:
    student = Student(**data.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def get_by_id(db: Session, student_id: int) -> Student | None:
    return db.get(Student, student_id)


def get_by_student_no(db: Session, student_no: str) -> Student | None:
    return db.execute(
        select(Student).where(Student.student_no == student_no)
    ).scalar_one_or_none()


def list_paginated(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    class_name: str | None = None,
) -> tuple[list[Student], int]:
    q = select(Student)
    count_q = select(func.count(Student.id))

    if keyword:
        pattern = f"%{keyword}%"
        cond = or_(
            Student.name.like(pattern),
            Student.student_no.like(pattern),
        )
        q = q.where(cond)
        count_q = count_q.where(cond)

    if class_name:
        q = q.where(Student.class_name == class_name)
        count_q = count_q.where(Student.class_name == class_name)

    total = db.execute(count_q).scalar()
    items = db.execute(
        q.order_by(Student.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return list(items), total


def update(db: Session, student_id: int, data: StudentUpdate) -> Student | None:
    student = db.get(Student, student_id)
    if not student:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(student, k, v)
    db.commit()
    db.refresh(student)
    return student


def delete(db: Session, student_id: int) -> bool:
    student = db.get(Student, student_id)
    if not student:
        return False
    db.delete(student)
    db.commit()
    return True


def update_face_status(db: Session, student_id: int, status: int):
    student = db.get(Student, student_id)
    if student:
        student.face_status = status
        db.commit()


def count_all(db: Session) -> int:
    return db.execute(select(func.count(Student.id))).scalar()
'''

# ============================================================
# repository/face_repo.py
# ============================================================
FILES["repository/face_repo.py"] = '''\
"""Face repository — SQLite metadata + Milvus vector operations."""
import time
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.face import Face
from core.milvus import get_client
from core.config import settings
import numpy as np


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


def search_similar(embedding: np.ndarray, top_k: int = 5) -> list[dict]:
    c = get_client()
    results = c.search(
        collection_name=settings.COLLECTION_NAME,
        data=[embedding.tolist()],
        limit=top_k,
        output_fields=["face_id", "student_id", "created_at"],
        search_params={"metric_type": "COSINE"},
    )
    hits = []
    if results and results[0]:
        for hit in results[0]:
            hits.append({
                "face_id": hit.entity.get("face_id"),
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
'''

# ============================================================
# repository/notice_repo.py
# ============================================================
FILES["repository/notice_repo.py"] = '''\
"""Notice repository."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.notice import Notice
from schemas.notice import NoticeCreate, NoticeUpdate


def create(db: Session, data: NoticeCreate) -> Notice:
    notice = Notice(**data.model_dump())
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return notice


def get_by_id(db: Session, notice_id: int) -> Notice | None:
    return db.get(Notice, notice_id)


def list_paginated(db: Session, page: int = 1, page_size: int = 20) -> tuple[list[Notice], int]:
    total = db.execute(select(func.count(Notice.id))).scalar()
    items = db.execute(
        select(Notice).order_by(Notice.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total


def update(db: Session, notice_id: int, data: NoticeUpdate) -> Notice | None:
    notice = db.get(Notice, notice_id)
    if not notice:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(notice, k, v)
    db.commit()
    db.refresh(notice)
    return notice


def delete(db: Session, notice_id: int) -> bool:
    notice = db.get(Notice, notice_id)
    if not notice:
        return False
    db.delete(notice)
    db.commit()
    return True
'''

# ============================================================
# service/student_service.py
# ============================================================
FILES["service/student_service.py"] = '''\
"""Student service — business logic, cascade delete orchestration."""
import os
from sqlalchemy.orm import Session
from schemas.student import StudentCreate, StudentUpdate
from schemas.common import PageResult
from repository import student_repo, face_repo
from core.config import settings


def create_student(db: Session, data: StudentCreate) -> dict:
    existing = student_repo.get_by_student_no(db, data.student_no)
    if existing:
        return {"code": 1, "message": f"student_no {data.student_no} already exists"}
    student = student_repo.create(db, data)
    return {"code": 0, "data": _serialize(student)}


def get_student(db: Session, student_id: int) -> dict:
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}
    return {"code": 0, "data": _serialize(student)}


def list_students(db: Session, page: int, page_size: int, keyword: str | None, class_name: str | None) -> dict:
    items, total = student_repo.list_paginated(db, page, page_size, keyword, class_name)
    return {"code": 0, "data": {
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(s) for s in items],
    }}


def update_student(db: Session, student_id: int, data: StudentUpdate) -> dict:
    student = student_repo.update(db, student_id, data)
    if not student:
        return {"code": 1, "message": "student not found"}
    return {"code": 0, "data": _serialize(student)}


def delete_student(db: Session, student_id: int) -> dict:
    """
    Cascade delete: SQLite (CASCADE) + Milvus (manual) + disk files (manual).
    Orchestrated here in Service layer.
    """
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}

    # 1. get all face_ids before SQLite cascade-delete wipes them
    face_ids = face_repo.get_all_face_ids_by_student(db, student_id)

    # 2. get all image paths before cascade
    faces = face_repo.get_faces_by_student(db, student_id)
    image_paths = [f.image_path for f in faces]

    # 3. delete SQLite record (faces table auto-cascade via FK ON DELETE CASCADE)
    student_repo.delete(db, student_id)

    # 4. delete Milvus vectors
    if face_ids:
        face_repo.delete_vectors(face_ids)

    # 5. delete disk image files
    for path in image_paths:
        full_path = os.path.join(str(settings.BASE_DIR), path.lstrip("/"))
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception:
            pass  # log but don't fail the whole operation

    return {"code": 0, "message": "student and all associated faces deleted"}


def import_students_csv(db: Session, file_path: str) -> dict:
    """Batch import students from CSV/Excel."""
    import pandas as pd
    df = pd.read_csv(file_path)
    required = ["student_no", "name"]
    for col in required:
        if col not in df.columns:
            return {"code": 1, "message": f"missing column: {col}"}

    imported, skipped = 0, 0
    for _, row in df.iterrows():
        existing = student_repo.get_by_student_no(db, str(row["student_no"]))
        if existing:
            skipped += 1
            continue
        data = StudentCreate(
            student_no=str(row["student_no"]),
            name=str(row["name"]),
            gender=str(row.get("gender", "")) or None,
            class_name=str(row.get("class_name", "")) or None,
            major=str(row.get("major", "")) or None,
            enroll_year=int(row["enroll_year"]) if "enroll_year" in row and pd.notna(row.get("enroll_year")) else None,
            phone=str(row.get("phone", "")) or None,
        )
        student_repo.create(db, data)
        imported += 1

    return {"code": 0, "data": {"imported": imported, "skipped": skipped}}


def _serialize(student) -> dict:
    return {
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "gender": student.gender,
        "class_name": student.class_name,
        "major": student.major,
        "enroll_year": student.enroll_year,
        "phone": student.phone,
        "face_status": student.face_status,
        "created_at": str(student.created_at) if student.created_at else None,
        "updated_at": str(student.updated_at) if student.updated_at else None,
    }
'''

# ============================================================
# service/face_service.py
# ============================================================
FILES["service/face_service.py"] = '''\
"""Face service — camera capture -> detect -> embed -> store (3 places)."""
import os
import cv2
import numpy as np
from sqlalchemy.orm import Session
from core.config import settings
from core.face_engine import detect_and_embed
from repository import student_repo, face_repo


def enroll_face(db: Session, student_id: int, image_bytes: bytes) -> dict:
    """
    Full pipeline: decode -> detect -> embed -> store.
    Stores: disk image + Milvus vector + SQLite metadata.
    """
    student = student_repo.get_by_id(db, student_id)
    if not student:
        return {"code": 1, "message": "student not found"}

    # 1. decode bytes to BGR numpy array
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"code": 1, "message": "invalid image data"}

    # 2. detect face + extract embedding
    result = detect_and_embed(img)
    if result is None:
        return {"code": 0, "data": {"detected": False, "reason": "no face detected"}}

    embedding = result["embedding"]

    # 3. save image to disk (by student_no subdirectory)
    student_dir = os.path.join(str(settings.FACE_IMG_DIR), student.student_no)
    os.makedirs(student_dir, exist_ok=True)
    import time
    filename = f"{int(time.time() * 1000)}.jpg"
    img_path = os.path.join(student_dir, filename)
    cv2.imwrite(img_path, img)

    # relative path for storage
    rel_path = f"/uploads/faces/{student.student_no}/{filename}"

    # 4. create SQLite metadata record first (to get auto-increment id as face_id)
    face = face_repo.create_face_record(db, student_id, rel_path)
    # use the SQLite auto-increment id as Milvus face_id (ensures 1:1 mapping)
    face_repo_face_id = face.id

    # update face_id in the record
    face.face_id = face_repo_face_id
    from models.face import Face
    db_face = db.get(Face, face.id)
    db_face.face_id = face_repo_face_id
    db.commit()
    db.refresh(db_face)

    # 5. insert vector into Milvus
    face_repo.insert_vector(face_repo_face_id, student_id, embedding)

    # 6. update student face_status
    student_repo.update_face_status(db, student_id, 1)

    return {"code": 0, "data": {
        "detected": True,
        "face_id": face_repo_face_id,
        "image_path": rel_path,
    }}


def get_student_faces(db: Session, student_id: int) -> dict:
    faces = face_repo.get_faces_by_student(db, student_id)
    return {"code": 0, "data": [_serialize_face(f) for f in faces]}


def delete_face(db: Session, face_id: int) -> dict:
    """Delete face: SQLite + Milvus + disk."""
    face = face_repo.get_face_by_id(db, face_id)
    if not face:
        return {"code": 1, "message": "face not found"}

    student_id = face.student_id
    image_path = face.image_path

    # 1. delete SQLite record
    face_repo.delete_face_record(db, face_id)

    # 2. delete Milvus vector
    face_repo.delete_vectors([face_id])

    # 3. delete disk file
    full_path = os.path.join(str(settings.BASE_DIR), image_path.lstrip("/"))
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass

    # 4. update student face_status if no faces left
    remaining = face_repo.count_faces_by_student(db, student_id)
    if remaining == 0:
        student_repo.update_face_status(db, student_id, 0)

    return {"code": 0, "message": "face deleted"}


def _serialize_face(face) -> dict:
    return {
        "id": face.id,
        "face_id": face.face_id,
        "student_id": face.student_id,
        "image_path": face.image_path,
        "created_at": str(face.created_at) if face.created_at else None,
    }
'''

# ============================================================
# service/stats_service.py
# ============================================================
FILES["service/stats_service.py"] = '''\
"""Statistics service — summary, face coverage, face count distribution."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models.student import Student
from models.face import Face


def get_summary(db: Session) -> dict:
    total = db.execute(select(func.count(Student.id))).scalar()

    by_class = {}
    rows = db.execute(
        select(Student.class_name, func.count(Student.id))
        .group_by(Student.class_name)
    ).all()
    for name, cnt in rows:
        if name:
            by_class[name] = cnt

    by_gender = {}
    rows = db.execute(
        select(Student.gender, func.count(Student.id))
        .group_by(Student.gender)
    ).all()
    for name, cnt in rows:
        if name:
            by_gender[name] = cnt

    by_major = {}
    rows = db.execute(
        select(Student.major, func.count(Student.id))
        .group_by(Student.major)
    ).all()
    for name, cnt in rows:
        if name:
            by_major[name] = cnt

    return {"code": 0, "data": {
        "total_students": total,
        "by_class": by_class,
        "by_gender": by_gender,
        "by_major": by_major,
    }}


def get_face_coverage(db: Session) -> dict:
    enrolled = db.execute(
        select(func.count(Student.id)).where(Student.face_status == 1)
    ).scalar()
    not_enrolled = db.execute(
        select(func.count(Student.id)).where(Student.face_status == 0)
    ).scalar()
    total = enrolled + not_enrolled
    rate = round(enrolled / total, 4) if total > 0 else 0

    return {"code": 0, "data": {
        "enrolled": enrolled,
        "not_enrolled": not_enrolled,
        "coverage_rate": rate,
    }}


def get_face_count(db: Session) -> dict:
    # count students with 0, 1, or multiple faces
    face_counts = db.execute(
        select(Face.student_id, func.count(Face.id))
        .group_by(Face.student_id)
    ).all()
    face_map = {sid: cnt for sid, cnt in face_counts}

    total_students = db.execute(select(func.count(Student.id))).scalar()
    zero_face = total_students - len(face_map)
    one_face = sum(1 for c in face_map.values() if c == 1)
    multiple = sum(1 for c in face_map.values() if c > 1)

    return {"code": 0, "data": {
        "zero_face": zero_face,
        "one_face": one_face,
        "multiple_faces": multiple,
    }}
'''

# ============================================================
# service/cluster_service.py
# ============================================================
FILES["service/cluster_service.py"] = '''\
"""ML service — face embedding clustering for face library audit.

Pulls all vectors from Milvus -> N x N cosine similarity matrix ->
hierarchical clustering -> detect anomalies:
  - same student, similarity < 0.6  => dirty data (wrong person)
  - different students, similarity > 0.85 => duplicate registration
"""
import numpy as np
from scipy.cluster.hierarchy import linkage, fcluster
from repository import face_repo
from sqlalchemy.orm import Session
from models.student import Student
from models.face import Face


def audit_duplicates(db: Session, same_student_threshold: float = 0.6, cross_student_threshold: float = 0.85) -> dict:
    """
    Run face library audit using embedding clustering.

    Returns:
      - intra_student_low: same student, low similarity (dirty data)
      - cross_student_high: different students, high similarity (dupes)
    """
    all_vectors = face_repo.get_all_vectors()
    if len(all_vectors) < 2:
        return {"code": 0, "data": {"duplicates": [], "dirty_data": [], "total_vectors": len(all_vectors)}}

    # build matrices
    face_ids = [v["face_id"] for v in all_vectors]
    student_ids = [v["student_id"] for v in all_vectors]
    embeddings = np.array([v["embedding"] for v in all_vectors])

    # normalize for cosine similarity (dot product of normalized vectors = cosine)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / (norms + 1e-8)

    # N x N cosine similarity matrix
    sim_matrix = normalized @ normalized.T

    # ---- check 1: same student, low similarity (dirty data) ----
    dirty_data = []
    n = len(face_ids)
    for i in range(n):
        for j in range(i + 1, n):
            if student_ids[i] == student_ids[j]:
                sim = float(sim_matrix[i, j])
                if sim < same_student_threshold:
                    s = db.get(Student, student_ids[i])
                    dirty_data.append({
                        "student": {"id": student_ids[i], "name": s.name if s else "?", "student_no": s.student_no if s else "?"},
                        "face_a_id": face_ids[i],
                        "face_b_id": face_ids[j],
                        "similarity": round(sim, 4),
                        "issue": "same student, low similarity — possible wrong face recorded",
                    })

    # ---- check 2: different students, high similarity (dupes) ----
    duplicates = []
    for i in range(n):
        for j in range(i + 1, n):
            if student_ids[i] != student_ids[j]:
                sim = float(sim_matrix[i, j])
                if sim > cross_student_threshold:
                    sa = db.get(Student, student_ids[i])
                    sb = db.get(Student, student_ids[j])
                    duplicates.append({
                        "student_a": {"id": student_ids[i], "name": sa.name if sa else "?", "student_no": sa.student_no if sa else "?"},
                        "student_b": {"id": student_ids[j], "name": sb.name if sb else "?", "student_no": sb.student_no if sb else "?"},
                        "similarity": round(sim, 4),
                    })

    return {"code": 0, "data": {
        "duplicates": duplicates,
        "dirty_data": dirty_data,
        "total_vectors": n,
    }}
'''

# ============================================================
# service/notice_service.py
# ============================================================
FILES["service/notice_service.py"] = '''\
"""Notice service — CRUD + WebSocket broadcast orchestration."""
from sqlalchemy.orm import Session
from schemas.notice import NoticeCreate, NoticeUpdate
from repository import notice_repo
from api.notices import ws_manager


def create_notice(db: Session, data: NoticeCreate) -> dict:
    notice = notice_repo.create(db, data)
    result = _serialize(notice)

    # broadcast via WebSocket to all connected clients
    ws_manager.broadcast({"type": "notice", "data": result})

    return {"code": 0, "data": result}


def get_notice(db: Session, notice_id: int) -> dict:
    notice = notice_repo.get_by_id(db, notice_id)
    if not notice:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "data": _serialize(notice)}


def list_notices(db: Session, page: int, page_size: int) -> dict:
    items, total = notice_repo.list_paginated(db, page, page_size)
    return {"code": 0, "data": {
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(n) for n in items],
    }}


def update_notice(db: Session, notice_id: int, data: NoticeUpdate) -> dict:
    notice = notice_repo.update(db, notice_id, data)
    if not notice:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "data": _serialize(notice)}


def delete_notice(db: Session, notice_id: int) -> dict:
    ok = notice_repo.delete(db, notice_id)
    if not ok:
        return {"code": 1, "message": "notice not found"}
    return {"code": 0, "message": "notice deleted"}


def _serialize(notice) -> dict:
    return {
        "id": notice.id,
        "title": notice.title,
        "content": notice.content,
        "publisher": notice.publisher,
        "created_at": str(notice.created_at) if notice.created_at else None,
    }
'''

# ============================================================
# api/deps.py
# ============================================================
FILES["api/deps.py"] = '''\
"""Dependency injection (corresponds to @Autowired)."""
from sqlalchemy.orm import Session
from core.db import get_db


def get_db_session() -> Session:
    """Yield a database session for request scope."""
    yield from get_db()
'''

# ============================================================
# api/notices.py (WebSocket manager + REST)
# ============================================================
FILES["api/notices.py"] = '''\
"""Notice controller — REST CRUD + WebSocket real-time push."""
import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, UploadFile, File
from sqlalchemy.orm import Session
from api.deps import get_db_session
from schemas.notice import NoticeCreate, NoticeUpdate
from service import notice_service

router = APIRouter(prefix="/api/notices", tags=["notices"])


class ConnectionManager:
    """Manages WebSocket connections for notice broadcasting."""
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast_async(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast(self, message: dict):
        """Sync wrapper — called from sync service code."""
        if not self.active:
            return
        loop = asyncio.new_event_loop()
        try:
            tasks = [ws.send_json(message) for ws in self.active]
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        finally:
            loop.close()


ws_manager = ConnectionManager()


@router.get("")
def list_notices(page: int = 1, page_size: int = 20, db: Session = Depends(get_db_session)):
    return notice_service.list_notices(db, page, page_size)


@router.get("/{notice_id}")
def get_notice(notice_id: int, db: Session = Depends(get_db_session)):
    return notice_service.get_notice(db, notice_id)


@router.post("")
def create_notice(data: NoticeCreate, db: Session = Depends(get_db_session)):
    return notice_service.create_notice(db, data)


@router.put("/{notice_id}")
def update_notice(notice_id: int, data: NoticeUpdate, db: Session = Depends(get_db_session)):
    return notice_service.update_notice(db, notice_id, data)


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, db: Session = Depends(get_db_session)):
    return notice_service.delete_notice(db, notice_id)


# ---- WebSocket endpoint ----
ws_router = APIRouter()


@ws_router.websocket("/ws/notices")
async def websocket_notices(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "pong":
                continue  # heartbeat response, ignore
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
'''

# ============================================================
# api/students.py
# ============================================================
FILES["api/students.py"] = '''\
"""Student controller (corresponds to @RestController)."""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from api.deps import get_db_session
from schemas.student import StudentCreate, StudentUpdate
from service import student_service
from core.enums import EnumContainer

router = APIRouter(prefix="/api/students", tags=["students"])


@router.get("")
def list_students(
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    class_name: str | None = None,
    db: Session = Depends(get_db_session),
):
    return student_service.list_students(db, page, page_size, keyword, class_name)


@router.get("/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db_session)):
    return student_service.get_student(db, student_id)


@router.post("")
def create_student(data: StudentCreate, db: Session = Depends(get_db_session)):
    return student_service.create_student(db, data)


@router.put("/{student_id}")
def update_student(student_id: int, data: StudentUpdate, db: Session = Depends(get_db_session)):
    return student_service.update_student(db, student_id, data)


@router.delete("/{student_id}")
def delete_student(student_id: int, db: Session = Depends(get_db_session)):
    return student_service.delete_student(db, student_id)


@router.post("/import")
async def import_students(file: UploadFile = File(...), db: Session = Depends(get_db_session)):
    import tempfile, os
    suffix = os.path.splitext(file.filename)[1] or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return student_service.import_students_csv(db, tmp_path)
    finally:
        os.unlink(tmp_path)


# ---- enum endpoint ----
enum_router = APIRouter(prefix="/api/enums", tags=["enums"])


@enum_router.get("")
def get_enums():
    return {"code": 0, "data": {
        "classes": EnumContainer.get_classes(),
        "majors": EnumContainer.get_majors(),
    }}
'''

# ============================================================
# api/faces.py
# ============================================================
FILES["api/faces.py"] = '''\
"""Face controller — enroll + list + delete."""
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import face_service

router = APIRouter(prefix="/api", tags=["faces"])


@router.post("/students/{student_id}/faces")
async def enroll_face(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session),
):
    image_bytes = await file.read()
    return face_service.enroll_face(db, student_id, image_bytes)


@router.get("/students/{student_id}/faces")
def get_faces(student_id: int, db: Session = Depends(get_db_session)):
    return face_service.get_student_faces(db, student_id)


@router.delete("/faces/{face_id}")
def delete_face(face_id: int, db: Session = Depends(get_db_session)):
    return face_service.delete_face(db, face_id)
'''

# ============================================================
# api/stats.py
# ============================================================
FILES["api/stats.py"] = '''\
"""Statistics controller — summary, coverage, face count."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db_session)):
    return stats_service.get_summary(db)


@router.get("/face-coverage")
def get_face_coverage(db: Session = Depends(get_db_session)):
    return stats_service.get_face_coverage(db)


@router.get("/face-count")
def get_face_count(db: Session = Depends(get_db_session)):
    return stats_service.get_face_count(db)
'''

# ============================================================
# api/audit.py
# ============================================================
FILES["api/audit.py"] = '''\
"""Audit controller — ML face library audit (clustering results)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from api.deps import get_db_session
from service import cluster_service

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/duplicates")
def audit_duplicates(db: Session = Depends(get_db_session)):
    """Face library audit: detect dirty data and duplicate registrations."""
    return cluster_service.audit_duplicates(db)
'''

# ============================================================
# main.py
# ============================================================
FILES["main.py"] = '''\
"""Application entry point — assemble routes + middleware + startup events."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from core.config import settings
from core.db import init_db
from core.enums import EnumContainer
from core.milvus import get_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    print("[startup] loading enum data from CSV...")
    EnumContainer.load()
    print(f"  classes: {EnumContainer.get_classes()}")
    print(f"  majors: {EnumContainer.get_majors()}")

    print("[startup] initializing SQLite tables...")
    init_db()

    print("[startup] connecting Milvus Lite...")
    get_client()
    print(f"  collection: {settings.COLLECTION_NAME}")

    print("[startup] loading insightface model (buffalo_l)...")
    from core.face_engine import get_face_app
    get_face_app()
    print("[startup] all ready.")

    yield

    # ---- shutdown ----
    print("[shutdown] cleaning up...")


app = FastAPI(title="Face Attendance System", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# static files for face images
app.mount("/uploads", StaticFiles(directory=str(settings.FACE_IMG_DIR)), name="uploads")

# register routers
from api.students import router as students_router, enum_router
from api.faces import router as faces_router
from api.stats import router as stats_router
from api.audit import router as audit_router
from api.notices import router as notices_router, ws_router

app.include_router(students_router)
app.include_router(enum_router)
app.include_router(faces_router)
app.include_router(stats_router)
app.include_router(audit_router)
app.include_router(notices_router)
app.include_router(ws_router)


@app.get("/")
def root():
    return {"code": 0, "message": "Face Attendance System API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
'''

# ---- write all files ----
for rel_path, content in FILES.items():
    full_path = os.path.join(BASE, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  wrote {rel_path}")

print(f"\nDone! {len(FILES)} files generated.")
