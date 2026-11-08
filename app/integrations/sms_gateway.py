"""SMS Gateway for Android (https://github.com/capcom6/android-sms-gateway) 클라이언트.

클라이언트 갤럭시 폰에 앱을 설치하면 그 폰의 LG U+ 번호로 문자가 발송/수신된다.
"""

import hashlib
import hmac
import logging

import httpx

from app.utils import normalize_phone, to_e164

logger = logging.getLogger(__name__)


class SmsGateway:
    def __init__(self, base_url: str, username: str, password: str, signing_key: str = ""):
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), auth=(username, password), timeout=15)
        self._signing_key = signing_key

    async def send(self, phone: str, text: str) -> str | None:
        resp = await self._client.post(
            "/messages",
            json={"textMessage": {"text": text}, "phoneNumbers": [to_e164(phone)]},
        )
        resp.raise_for_status()
        return resp.json().get("id")

    async def register_webhook(self, url: str, event: str = "sms:received") -> dict:
        resp = await self._client.post("/webhooks", json={"url": url, "event": event})
        resp.raise_for_status()
        return resp.json()

    def verify_signature(self, body: bytes, timestamp: str | None, signature: str | None) -> bool:
        if not self._signing_key:
            return True
        if not timestamp or not signature:
            return False
        expected = hmac.new(self._signing_key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def aclose(self) -> None:
        await self._client.aclose()


class MacroDroidGateway:
    """폰의 MacroDroid '웹훅(URL)' 트리거를 호출하면 매크로가 'SMS 보내기'로 발송한다.

    send_url 예: https://trigger.macrodroid.com/<기기ID>/ai-send
    매크로에서 phone, text 파라미터를 받아 쓴다.
    """

    def __init__(self, send_url: str, send_key: str = ""):
        self._send_url = send_url
        self._send_key = send_key
        self._client = httpx.AsyncClient(timeout=20)

    async def send(self, phone: str, text: str) -> str | None:
        if not self._send_url:
            raise RuntimeError(".env 의 MACRODROID_SEND_URL 이 비어 있습니다.")
        params = {"phone": normalize_phone(phone), "text": text}
        if self._send_key:
            params["key"] = self._send_key
        resp = await self._client.get(self._send_url, params=params)
        resp.raise_for_status()
        return None

    async def register_webhook(self, url: str, event: str = "sms:received") -> dict:
        raise NotImplementedError("MacroDroid는 폰의 매크로에서 서버 주소를 직접 입력합니다.")

    def verify_signature(self, body: bytes, timestamp: str | None, signature: str | None) -> bool:
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


class MockSmsGateway:
    """MOCK_MODE용. 실제 발송 없이 기록만 남긴다."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send(self, phone: str, text: str) -> str | None:
        logger.info("[MOCK 발송] %s: %s", phone, text)
        self.sent.append((phone, text))
        return None

    async def register_webhook(self, url: str, event: str = "sms:received") -> dict:
        return {"mock": True}

    def verify_signature(self, body: bytes, timestamp: str | None, signature: str | None) -> bool:
        return True

    async def aclose(self) -> None:
        pass
