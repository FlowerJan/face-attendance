"""Milvus Lite client and collection management."""
import logging

from pymilvus import MilvusClient, DataType
from core.config import settings

logger = logging.getLogger(__name__)
_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(settings.MILVUS_DB_PATH)
        _ensure_collection()
    return _client


def _ensure_loaded(c: MilvusClient) -> None:
    """确保集合处于 loaded 状态。

    Milvus 进程重启后集合会变成 released，此时 insert 仍可写入，
    但 query / search 会抛 `Collection is in state 'released'`。
    因此每次取客户端都主动 load 一次（幂等，已加载时会抛可忽略的异常）。
    """
    try:
        c.load_collection(settings.COLLECTION_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[milvus] load_collection skipped: %s", exc)


def _ensure_collection():
    c = get_client()
    if c.has_collection(settings.COLLECTION_NAME):
        _ensure_loaded(c)
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
    _ensure_loaded(c)


def backend_name() -> str:
    """Return the active vector backend name (for status reporting)."""
    try:
        get_client()
        return "milvus-lite"
    except Exception:  # noqa: BLE001
        return "unavailable"


def drop_collection():
    """Drop and recreate (for testing/reset)."""
    c = get_client()
    if c.has_collection(settings.COLLECTION_NAME):
        c.drop_collection(settings.COLLECTION_NAME)
    _ensure_collection()
