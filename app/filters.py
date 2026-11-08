"""AI가 응답해도 되는 메시지인지 판단. 개인 휴대폰 번호라 지인/광고/인증문자를 반드시 걸러야 한다."""

from dataclasses import dataclass

from app.utils import is_korean_mobile

SPAM_MARKERS = ("(광고)", "[광고]", "무료거부", "수신거부", "[국외발신]", "[국제발신]")
SYSTEM_MARKERS = ("인증번호", "인증 번호", "본인확인", "승인번호")


@dataclass(frozen=True)
class FilterResult:
    allowed: bool
    reason: str = ""


def evaluate_inbound(
    phone: str,
    text: str,
    *,
    paused: bool,
    excluded: set[str],
    test_numbers: set[str],
    recent_outgoing: int,
    max_per_hour: int,
    check_rate_limit: bool = True,
) -> FilterResult:
    if paused:
        return FilterResult(False, "paused")
    if not text.strip():
        return FilterResult(False, "empty")
    if not is_korean_mobile(phone):
        return FilterResult(False, "not_mobile")
    if phone in excluded:
        return FilterResult(False, "excluded")
    if test_numbers and phone not in test_numbers:
        return FilterResult(False, "not_test_number")
    if any(marker in text for marker in SPAM_MARKERS):
        return FilterResult(False, "spam")
    if any(marker in text for marker in SYSTEM_MARKERS):
        return FilterResult(False, "system_message")
    if check_rate_limit and recent_outgoing >= max_per_hour:
        return FilterResult(False, "rate_limited")
    return FilterResult(True)
