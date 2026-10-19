"""WebSocket 连接池（下沉到 core 层，避免 api <-> service 循环导入）

- api 层负责握手/接收
- service 层（同步函数）通过 broadcast_sync 触发广播
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # 启动时由 lifespan 注入主事件循环，供同步代码跨线程广播使用
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.info("[WS] client connected, online=%d", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)
        logger.info("[WS] client disconnected, online=%d", len(self.active))

    async def send_text(self, ws: WebSocket, text: str) -> None:
        await ws.send_text(text)

    async def broadcast_async(self, message: dict[str, Any]) -> int:
        import json

        text = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for ws in list(self.active):
            try:
                await ws.send_text(text)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        return sent

    def broadcast(self, message: dict[str, Any]) -> int:
        """同步入口：在 FastAPI 的线程池里调用时，把协程丢回主事件循环执行"""
        if not self.active:
            return 0
        if self._loop is None or self._loop.is_closed():
            logger.warning("[WS] 事件循环未绑定，跳过广播")
            return 0
        future = asyncio.run_coroutine_threadsafe(self.broadcast_async(message), self._loop)
        try:
            return int(future.result(timeout=5))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WS] 广播失败: %s", exc)
            return 0


ws_manager = ConnectionManager()
