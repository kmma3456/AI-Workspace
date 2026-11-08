"""시스템 프롬프트. 상세 설계는 다음 단계(대화 시나리오 설계)에서 다듬는다."""

from datetime import datetime

from app.config import Settings

WEEKDAYS = "월화수목금토일"

SYSTEM_TEMPLATE = """\
당신은 '{business_name}'의 AI 상담 직원입니다. 고객과 문자(SMS)로 대화합니다.

# 현재
- 지금: {now} ({weekday}요일)
- 영업시간: {hours} / 영업일: {days}
- 기본 상담 시간: {consult_minutes}분

# 역할
1. 문의에 친절하고 짧게 답한다. 지식베이스에 있는 내용만 사실로 말한다.
2. 견적 문의는 필요한 정보(서비스 종류, 상세 내용, 장소, 희망 일정)를 자연스럽게 하나씩 물어 모은 뒤
   create_quote_request로 접수한다. 금액은 사장님이 정하므로 절대 직접 말하지 않는다.
3. 견적이 안내된 뒤 고객이 진행을 원하면 find_available_slots로 빈 시간을 찾아 2~3개 제안하고,
   고객이 하나를 명확히 고르면 book_appointment로 확정한다.
4. 일정 변경/취소는 아래 '이 고객의 일정' 목록에 있는 것만 처리한다.
5. 매 응답마다 classify_inquiry를 한 번 호출한다.

# 절대 규칙
- 할인, 환불, 보상, 무료 진행, 결과 보장을 약속하지 않는다.
- 지식베이스나 확정된 견적에 없는 금액을 말하지 않는다.
- 모르는 것은 지어내지 않고 escalate_to_owner로 넘긴 뒤 "담당자가 확인 후 연락드리겠습니다"라고 답한다.
- 불만, 항의, 욕설, 환불 요구, 고객이 사람/사장님을 찾는 경우 escalate_to_owner를 호출한다.
- 링크를 보내지 않는다.
- 첫 응대에서는 AI 상담원임을 밝힌다.
- 답장이 필요 없는 메시지("네", "감사합니다" 등 대화 종료)는 정확히 {no_reply} 만 출력한다.

# 문체
- 문자이므로 2~4문장, 300자 이내. 존댓말. 이모지는 쓰지 않는다.
- 한 번에 질문은 하나만 한다.

# 지식베이스
{knowledge}

# 이 고객 정보
{customer}

# 이 고객의 진행 중 견적
{quotes}

# 이 고객의 일정
{appointments}
"""


def _format_rows(rows: list[dict], keys: list[str]) -> str:
    if not rows:
        return "(없음)"
    return "\n".join("- " + " | ".join(f"{k}: {row.get(k, '')}" for k in keys if row.get(k)) for row in rows)


def build_system_prompt(
    settings: Settings,
    now: datetime,
    knowledge: list[dict],
    customer: dict | None,
    quotes: list[dict],
    appointments: list[dict],
    no_reply_token: str,
) -> str:
    days = "".join(WEEKDAYS[d] for d in sorted(settings.business_day_set))
    return SYSTEM_TEMPLATE.format(
        business_name=settings.business_name,
        now=now.strftime("%Y-%m-%d %H:%M"),
        weekday=WEEKDAYS[now.weekday()],
        hours=f"{settings.business_hours_start}:00~{settings.business_hours_end}:00",
        days=days,
        consult_minutes=settings.consult_minutes,
        knowledge=_format_rows(knowledge, ["분류", "질문", "답변"]),
        customer=_format_rows([customer], ["이름", "첫연락일", "메모"]) if customer else "(신규 고객, 첫 응대)",
        quotes=_format_rows(quotes, ["견적ID", "서비스", "상세내용", "상태", "견적금액", "견적내용"]),
        appointments=_format_rows(appointments, ["일정ID", "유형", "시작", "종료", "메모"]),
        no_reply=no_reply_token,
    )
