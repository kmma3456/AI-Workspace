import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

TOKEN = "test-token"


@pytest.fixture
def client(monkeypatch):
    for key, value in {
        "MOCK_MODE": "true", "ANTHROPIC_API_KEY": "x", "WEBHOOK_TOKEN": TOKEN, "BATCH_WINDOW_SECONDS": "60",
        "TEST_NUMBERS": "",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_sms_requires_token(client):
    assert client.post("/webhooks/macrodroid/sms", params={"phone": "01012345678", "text": "hi"}).status_code == 401


def test_sms_via_query_params_is_batched(client):
    res = client.post("/webhooks/macrodroid/sms", params={"phone": "+82 10-1234-5678", "text": "견적 문의요", "token": TOKEN})
    assert res.json() == {"handled": True}
    assert "01012345678" in client.app.state.batcher._buffers


def test_sms_via_json_body(client):
    res = client.post("/webhooks/macrodroid/sms", headers={"X-Webhook-Token": TOKEN},
                      json={"phone": "010-2222-3333", "text": "안녕하세요 \"따옴표\"\n줄바꿈"})
    assert res.json() == {"handled": True}


def test_duplicate_within_a_minute_ignored(client):
    params = {"phone": "01012345678", "text": "같은 문자", "token": TOKEN}
    client.post("/webhooks/macrodroid/sms", params=params)
    assert client.post("/webhooks/macrodroid/sms", params=params).json() == {"duplicate": True}


def test_carrier_notice_through_sms_endpoint(client):
    res = client.post("/webhooks/macrodroid/sms", params={
        "phone": "15440010", "text": "[콜키퍼] 010-7777-8888님이 전화하셨습니다.", "token": TOKEN,
    })
    assert res.json() == {"missed_call_notice": ["01077778888"]}


def test_missed_call(client):
    res = client.get("/webhooks/macrodroid/missed-call", params={"phone": "01055556666", "token": TOKEN})
    assert res.json() == {"accepted": True, "sent": True}
    [message] = client.app.state.service.deps.db.history("01055556666", 5)
    assert message["source"] == "template"


def test_missed_call_menu_after_ten_digit_inbound(client):
    client.get("/webhooks/macrodroid/missed-call", params={"phone": "01077778888", "token": TOKEN})
    res = client.post(
        "/webhooks/macrodroid/sms",
        params={"phone": "1077778888", "text": "2", "token": TOKEN},
    )
    body = res.json()
    assert body.get("missed_call_intake") == "branch_prompt"
    assert body.get("category") == "2"


def test_api_requires_admin_login(client):
    assert client.get("/api/status").status_code == 401


def test_api_status_with_admin_session(client):
    login = client.post("/api/auth/login", json={"email": "test@naver.com", "password": "12341234"})
    assert login.status_code == 200
    assert client.get("/api/status", cookies=login.cookies).status_code == 200


def test_admin_blocked_through_tunnel(client):
    login = client.post("/api/auth/login", json={"email": "test@naver.com", "password": "12341234"})
    assert client.get("/api/status", cookies=login.cookies, headers={"CF-Connecting-IP": "1.2.3.4"}).status_code == 403
    assert client.get("/ui/admin.html", headers={"CF-Connecting-IP": "1.2.3.4"}).status_code == 403
    tunneled = client.get(
        "/webhooks/macrodroid/missed-call",
        params={"phone": "01055556666", "token": TOKEN},
        headers={"CF-Connecting-IP": "1.2.3.4"},
    )
    assert tunneled.status_code == 200
