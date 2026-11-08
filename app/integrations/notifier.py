"""사장님 알림 (텔레그램 봇). 넘기기, 견적 요청, 일일 리포트, DRY_RUN 미리보기에 쓴다.

최근 알림은 관리 화면에서도 볼 수 있도록 메모리에 보관한다.
"""

import logging
from collections import deque
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class OwnerNotifier:
    def __init__(self, bot_token: str, chat_id: str, tz=None, keep: int = 200):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._tz = tz
        self._client = httpx.AsyncClient(timeout=10)
        self.recent: deque[dict] = deque(maxlen=keep)

    async def notify(self, text: str) -> None:
        self.recent.appendleft({"at": datetime.now(self._tz).strftime("%m-%d %H:%M:%S"), "text": text})
        if not self._bot_token or not self._chat_id:
            logger.info("[사장님 알림] %s", text)
            return
        try:
            resp = await self._client.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text[:4000]},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("텔레그램 알림 실패")

    async def aclose(self) -> None:
        await self._client.aclose()
