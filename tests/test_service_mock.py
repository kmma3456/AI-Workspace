"""MOCK_MODE 전체 흐름: 견적 문의 -> 견적 접수 -> 사장님 견적 입력 -> 일정 확정."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.deps import build_deps
from app.integrations.sheets import QuoteStatus
from app.service import ConversationService

PHONE = "01012345678"


class ScriptedClaude:
    """도구 호출 응답과 최종 텍스트 응답을 차례대로 돌려준다."""

    def __init__(self):
        self.queue: list = []
        self.messages = SimpleNamespace(create=self._create)

    def then_tools(self, *calls):
        blocks = [SimpleNamespace(type="tool_use", id=f"tu_{i}", name=n, input=a) for i, (n, a) in enumerate(calls)]
        self.queue.append(SimpleNamespace(stop_reason="tool_use", content=blocks))
        return self

    def then_text(self, text):
        self.queue.append(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]))
        return self

    async def _create(self, **kwargs):
        return self.queue.pop(0)

    async def close(self):
        pass


def _next_business_day() -> date:
    day = date.today() + timedelta(days=3)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


@pytest.fixture
async def service():
    settings = Settings(_env_file=None, anthropic_api_key="x", webhook_token="t", mock_mode=True)
    deps = build_deps(settings)
    deps.claude = ScriptedClaude()
    yield ConversationService(deps)
    await deps.aclose()


@pytest.mark.asyncio
async def test_quote_to_booking_flow(service):
    claude = service.deps.claude
    sheets = service.deps.sheets

    claude.then_tools(
        ("classify_inquiry", {"category": "견적문의", "summary": "욕실 타일 교체 견적"}),
        ("save_customer_info", {"name": "김철수"}),
        ("create_quote_request", {"service": "욕실 타일 교체", "details": "욕실 1개, 벽+바닥", "location": "성남"}),
    ).then_text("김철수님, 견적 요청 접수했습니다. 담당자가 확인 후 견적 보내드리겠습니다.")
    outcome = await service.handle_inbound(PHONE, "욕실 타일 교체 견적 부탁드려요. 김철수입니다. 성남이에요")

    assert outcome.reply.startswith("김철수님")
    assert [t["tool"] for t in outcome.trace] == ["classify_inquiry", "save_customer_info", "create_quote_request"]
    [quote] = await sheets.quotes_by_status(QuoteStatus.WAITING)
    assert quote["이름"] == "김철수"

    await service.mark_quote_ready(quote["견적ID"], "1200000", "타일 철거 및 시공, 1일 소요")
    assert (await sheets.quotes_by_status(QuoteStatus.SENT))[0]["견적ID"] == quote["견적ID"]
    sent_texts = [t for p, t in service.deps.gateway.sent if p == PHONE]
    assert "1,200,000" in sent_texts[-1]
    assert "타일 철거" in sent_texts[-1]

    day = _next_business_day()
    start = f"{day.isoformat()}T13:00"
    claude.then_tools(
        ("classify_inquiry", {"category": "일정예약", "summary": "작업 일정 확정"}),
        ("book_appointment", {"start": start, "appointment_type": "작업", "duration_minutes": 240, "quote_id": quote["견적ID"]}),
    ).then_text(f"{day.month}월 {day.day}일 오후 1시로 확정했습니다.")
    outcome = await service.handle_inbound(PHONE, "네 그날 1시로 해주세요")

    assert not outcome.trace[1]["is_error"], outcome.trace[1]
    [appt] = await sheets.appointments_for(PHONE)
    assert appt["시작"] == start.replace("T", " ")
    assert (await sheets.quotes_by_status(QuoteStatus.SCHEDULED))[0]["견적ID"] == quote["견적ID"]
    assert any("[일정 등록]" in n["text"] for n in service.deps.notifier.recent)

    sources = [m["source"] for m in service.deps.db.history(PHONE, 50)]
    assert sources == ["customer", "ai", "owner", "customer", "ai"]


@pytest.mark.asyncio
async def test_blocked_reply_falls_back(service):
    service.deps.claude.then_text("특별히 10만원 할인해 드릴게요!")
    outcome = await service.handle_inbound(PHONE, "깎아주세요")
    assert outcome.blocked_reason
    assert outcome.reply != outcome.original_reply
    assert outcome.escalated


@pytest.mark.asyncio
async def test_booking_rejects_busy_slot(service):
    busy = next(e for e in await service.deps.calendar.events_between(
        datetime.now(service.settings.tz), datetime.now(service.settings.tz) + timedelta(days=5)))
    busy_start = datetime.fromisoformat(busy["start"]["dateTime"]).strftime("%Y-%m-%dT%H:%M")
    service.deps.claude.then_tools(
        ("book_appointment", {"start": busy_start, "appointment_type": "상담"}),
    ).then_text("다른 시간을 골라주세요.")
    outcome = await service.handle_inbound(PHONE, "그 시간에 상담할게요")
    assert outcome.trace[0]["is_error"]
