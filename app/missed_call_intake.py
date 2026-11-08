"""부재중 자동문자 이후 1·2·3 분기 응대 및 콜백 캘린더(마지막 문자 + 1시간)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

INTAKE_FLAG_PREFIX = "intake:"
INTAKE_CALENDAR_DELAY = timedelta(hours=1)
INTAKE_EVENT_DURATION = timedelta(minutes=30)

CATEGORY_LABELS = {
    "1": "중고차 성능보증 수리·점검",
    "2": "일반 정비·견적",
    "3": "지인/개인 통화",
}

REPLY_AFTER_CHOICE = {
    "1": (
        "중고차 성능보증 접수를 도와드리겠습니다. 빠르고 정확한 조회를 위해 아래 내용을 남겨주시면 "
        "확인 후 즉시 연락드리겠습니다.\n"
        "- 차량 번호:\n- 차종:\n- 보증보험사 및 주요 증상:"
    ),
    "2": (
        "일반 정비 및 견적 상담 접수입니다. 확인 후 상세 안내드리겠습니다.\n"
        "- 차량 번호:\n- 차종:\n- 정비 요청사항:"
    ),
    "3": "용건 남겨주시면 확인 즉시 바로 연락드리겠습니다.",
}

REPLY_DONE = "접수되었습니다. 확인 후 연락드리겠습니다. 감사합니다."
REPLY_BAD_MENU = "1, 2, 3 중 문의 번호 하나만 입력해 주세요."

# 부재중 초기 안내 문자 본문에 항상 포함 — intake 플래그 유실 시 1·2·3 응답 복구용
MISSED_CALL_MENU_MARKER = "원활한 상담 및 예약을 위해"
INTAKE_RECOVERY_WINDOW = timedelta(hours=72)


def intake_flag_key(phone: str) -> str:
    return f"{INTAKE_FLAG_PREFIX}{phone}"


def intake_phone_variants(phone: str) -> list[str]:
    """부재중 웹훅(010…)과 수신 문자 웹훅(10…)처럼 같은 번호가 다른 키로 저장된 경우."""
    from app.utils import normalize_phone

    p = normalize_phone(phone)
    if not p:
        return []
    out: list[str] = []
    for candidate in (p, f"0{p}" if len(p) == 10 and p.startswith("1") else "", p[1:] if p.startswith("0") and len(p) == 11 else ""):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def parse_menu_choice(text: str) -> str | None:
    t = re.sub(r"\s+", "", text.strip())
    if not t:
        return None
    if t[0] in "123" or t in ("1번", "2번", "3번", "①", "②", "③"):
        digit = t[0] if t[0] in "123" else {"①": "1", "②": "2", "③": "3"}.get(t, t[0])
        return digit if digit in "123" else None
    if re.fullmatch(r"[123]", t):
        return t
    return None


def load_intake(raw: str) -> dict[str, Any] | None:
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def dump_intake(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


@dataclass
class IntakeStep:
    handled: bool
    reply: str | None = None
    complete: bool = False
    calendar: dict[str, Any] | None = None


def build_calendar_summary(phone_display: str, category: str, lines: list[str]) -> tuple[str, str]:
    label = CATEGORY_LABELS.get(category, category)
    summary = f"[부재중 후속] {label} · {phone_display}"
    body = "\n".join(
        [
            f"분류: {label} ({category}번)",
            f"고객번호: {phone_display}",
            "--- 수집 내용 ---",
            *lines,
        ]
    )
    return summary, body


def new_intake_state(now: datetime) -> dict[str, Any]:
    iso = now.isoformat()
    return {"phase": "menu", "started_at": iso, "last_customer_at": iso, "lines": []}
