"""통신사 부재중 알림 문자(콜키퍼, 매너콜 등) 인식.

예: "[콜키퍼] 010-1234-5678님이 10/25 20:40에 전화하셨습니다."
통신사/상품마다 문구가 달라서, 실제 알림 문자를 받아보고 KEYWORDS나 발신번호 설정을 맞춰야 한다.
"""

import re
from dataclasses import dataclass

from app.utils import is_korean_mobile, normalize_phone

KEYWORDS = ("콜키퍼", "매너콜", "부재중", "전화하셨", "전화를 하셨", "전화 왔", "전화가 왔", "통화중 전화", "연락하셨")
_PHONE_RE = re.compile(r"(?<!\d)(0\d{1,2})[-.\s]?(\d{3,4})[-.\s]?(\d{4})(?!\d)")


@dataclass(frozen=True)
class MissedCallNotice:
    mobile_callers: list[str]
    other_callers: list[str]


def parse_missed_call_notice(sender: str, text: str, trusted_senders: set[str]) -> MissedCallNotice | None:
    """통신사 알림 문자면 발신자 번호들을 돌려주고, 아니면 None."""
    sender = normalize_phone(sender)
    if trusted_senders:
        if sender not in trusted_senders:
            return None
    elif is_korean_mobile(sender) or not any(k in text for k in KEYWORDS):
        return None

    callers: list[str] = []
    for match in _PHONE_RE.finditer(text):
        phone = "".join(match.groups())
        if phone != sender and phone not in callers:
            callers.append(phone)
    if not callers:
        return None
    return MissedCallNotice(
        mobile_callers=[p for p in callers if is_korean_mobile(p)],
        other_callers=[p for p in callers if not is_korean_mobile(p)],
    )
