"""业务异常：由业务层抛出，控制层统一转成 {"code":非0} 响应"""
from __future__ import annotations


class BusinessError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(BusinessError):
    def __init__(self, message: str = "数据不存在") -> None:
        super().__init__(message, code=404)
