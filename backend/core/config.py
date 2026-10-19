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
