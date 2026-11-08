from app.agent.guardrails import check_reply
from app.filters import evaluate_inbound
from app.utils import extract_amounts, is_korean_mobile, normalize_phone, to_e164


def test_phone_normalization():
    assert normalize_phone("+82 10-1234-5678") == "01012345678"
    assert normalize_phone("010.1234.5678") == "01012345678"
    assert normalize_phone("1012345678") == "01012345678"
    assert to_e164("010-1234-5678") == "+821012345678"
    assert is_korean_mobile("01012345678")
    assert not is_korean_mobile("15881234")


def test_extract_amounts():
    assert extract_amounts("기본 15만원, 추가 30,000원") == {150000, 30000}


def _evaluate(phone="01012345678", text="견적 문의드려요", **overrides):
    kwargs = dict(paused=False, excluded=set(), test_numbers=set(), recent_outgoing=0, max_per_hour=6)
    kwargs.update(overrides)
    return evaluate_inbound(phone, text, **kwargs)


def test_filter_allows_normal_customer():
    assert _evaluate().allowed


def test_filter_blocks():
    assert _evaluate(paused=True).reason == "paused"
    assert _evaluate(phone="15881234").reason == "not_mobile"
    assert _evaluate(excluded={"01012345678"}).reason == "excluded"
    assert _evaluate(test_numbers={"01099998888"}).reason == "not_test_number"
    assert _evaluate(text="(광고) 특가 안내").reason == "spam"
    assert _evaluate(text="인증번호 [123456]").reason == "system_message"
    assert _evaluate(recent_outgoing=6).reason == "rate_limited"


def test_guardrails():
    assert check_reply("상담 일정은 내일 오후 2시 가능합니다.", set()).ok
    assert check_reply("기본 출장비는 3만원입니다.", {30000}).ok
    assert not check_reply("기본 출장비는 5만원입니다.", {30000}).ok
    assert not check_reply("이번에는 할인해 드릴게요.", set()).ok
    assert not check_reply("환불 처리해 드리겠습니다.", set()).ok
    assert not check_reply("자세한 내용은 https://example.com", set()).ok
