from app.config import Settings
from app.deps import build_deps
from app.missed_call_notice import parse_missed_call_notice
from app.service import ConversationService


def test_recognizes_carrier_notice():
    notice = parse_missed_call_notice("1544-0010", "[콜키퍼] 010-1234-5678님이 10/25 20:40에 전화하셨습니다.", set())
    assert notice.mobile_callers == ["01012345678"]


def test_multiple_and_landline_callers():
    text = "[부재중 알림]\n010.1111.2222 (2회)\n02-345-6789 (1회)"
    notice = parse_missed_call_notice("15440010", text, set())
    assert notice.mobile_callers == ["01011112222"]
    assert notice.other_callers == ["023456789"]


def test_ignores_customer_message_mentioning_call():
    assert parse_missed_call_notice("01099998888", "아까 010-1234-5678로 전화했었는데요", set()) is None


def test_ignores_unrelated_service_sms():
    assert parse_missed_call_notice("15881234", "[택배] 운송장 1234-5678-9012 배송 출발", set()) is None


def test_trusted_senders_only():
    text = "010-1234-5678님 부재중 전화"
    assert parse_missed_call_notice("15440010", text, {"15449999"}) is None
    assert parse_missed_call_notice("15449999", text, {"15449999"}).mobile_callers == ["01012345678"]


async def test_notice_triggers_missed_call_reply():
    settings = Settings(_env_file=None, anthropic_api_key="x", webhook_token="t", mock_mode=True)
    deps = build_deps(settings)
    service = ConversationService(deps)
    try:
        await service.handle_missed_call_notice("15440010", "[콜키퍼] 010-1234-5678님이 전화하셨습니다.")
        [message] = deps.db.history("01012345678", 10)
        assert message["source"] == "template"
        [log] = await deps.sheets.records("문의로그")
        assert log["채널"] == "전화(통신사 알림)"

        await service.handle_missed_call_notice("15440010", "[콜키퍼] 010-1234-5678님이 전화하셨습니다.")
        assert len(deps.db.history("01012345678", 10)) == 1
    finally:
        await deps.aclose()
