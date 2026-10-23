"""Dependency injection + shared request helpers."""
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session
from core.db import get_db

# 上传图片守卫：无白名单/无上限的 await file.read() 会让任意大文件直接吃满内存，
# 也会让后续 cv2.imdecode 对着二进制垃圾白跑一次检测。
MAX_IMAGE_MB = 5
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/bmp")


def get_db_session() -> Session:
    """Yield a database session for request scope."""
    yield from get_db()


async def read_image_upload(file: UploadFile, max_mb: int = MAX_IMAGE_MB) -> bytes:
    """读取上传图片：先校验类型与大小，再读入内存。

    超限/类型不对时抛 413 / 415，由全局异常处理返回统一结构。
    注意 content_type 由客户端声明，只能当第一道闸——
    内容是否真是图片仍由 imdecode 失败兜底（"invalid image data"）。
    """
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail=f"unsupported file type: {content_type or 'unknown'}")
    limit = max_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"file too large (max {max_mb} MB)")
    return data
