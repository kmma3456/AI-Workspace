"""발송 직전 하드 규칙 검사. 프롬프트로 막는 것과 별개로 코드에서 한 번 더 막는다."""

import re
from dataclasses import dataclass

from app.utils import extract_amounts

MAX_REPLY_CHARS = 600

FORBIDDEN_PATTERNS = [
    (re.compile(r"할인\s*(해\s*드|해드|가능|적용)"), "할인 약속"),
    (re.compile(r"환불\s*(해\s*드|해드|처리해|가능)"), "환불 약속"),
    (re.compile(r"보상\s*(해\s*드|해드|가능)"), "보상 약속"),
    (re.compile(r"무료로\s*(해\s*드|해드|진행)"), "무료 약속"),
    (re.compile(r"(100\s*%|무조건|반드시)\s*(보장|책임)"), "보장 약속"),
    (re.compile(r"https?://"), "링크 포함"),
]

FALLBACK_REPLY = "문의 주셔서 감사합니다. 정확한 안내를 위해 담당자가 확인 후 연락드리겠습니다."


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""


def check_reply(reply: str, allowed_amounts: set[int]) -> GuardResult:
    if len(reply) > MAX_REPLY_CHARS:
        return GuardResult(False, f"답장이 너무 김 ({len(reply)}자)")
    for pattern, label in FORBIDDEN_PATTERNS:
        if pattern.search(reply):
            return GuardResult(False, label)
    unknown = extract_amounts(reply) - allowed_amounts
    if unknown:
        return GuardResult(False, f"근거 없는 금액: {sorted(unknown)}")
    return GuardResult(True)
