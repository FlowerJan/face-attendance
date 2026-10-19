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
