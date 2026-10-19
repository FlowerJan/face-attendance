"""统一响应包裹：{"code": 0, "message": "ok", "data": {...}}"""
from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def fail(message: str, code: int = 1, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}
