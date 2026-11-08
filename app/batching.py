"""고객이 문자를 여러 개로 끊어 보내는 경우를 모아서 한 번에 처리한다. 단일 프로세스 전제."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[str, str], Awaitable[object]]


class MessageBatcher:
    def __init__(self, window_seconds: float, handler: Handler):
        self._window = window_seconds
        self._handler = handler
        self._buffers: dict[str, list[str]] = {}
        self._timers: dict[str, asyncio.Task] = {}

    def add(self, phone: str, text: str) -> None:
        self._buffers.setdefault(phone, []).append(text)
        if timer := self._timers.get(phone):
            timer.cancel()
        self._timers[phone] = asyncio.create_task(self._flush_later(phone))

    async def _flush_later(self, phone: str) -> None:
        try:
            await asyncio.sleep(self._window)
        except asyncio.CancelledError:
            return
        await self._flush(phone)

    async def _flush(self, phone: str) -> None:
        texts = self._buffers.pop(phone, [])
        self._timers.pop(phone, None)
        if not texts:
            return
        try:
            await self._handler(phone, "\n".join(texts))
        except Exception:
            logger.exception("메시지 처리 실패: %s", phone)

    async def flush_all(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        for phone in list(self._buffers):
            await self._flush(phone)
