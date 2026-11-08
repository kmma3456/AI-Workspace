"""SMS Gateway 클라우드에 수신 웹훅을 등록한다.

python -m scripts.register_webhook https://<서버주소>/webhooks/sms
"""

import asyncio
import sys

from app.config import get_settings
from app.integrations.sms_gateway import SmsGateway


async def main(url: str) -> None:
    s = get_settings()
    gateway = SmsGateway(s.sms_gateway_base_url, s.sms_gateway_username, s.sms_gateway_password)
    try:
        print(await gateway.register_webhook(url))
    finally:
        await gateway.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scripts.register_webhook <url>")
    asyncio.run(main(sys.argv[1]))
