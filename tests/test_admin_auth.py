from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("WEBHOOK_TOKEN", "test-token")
    monkeypatch.setenv("DATABASE_PATH", ":memory:")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_login_and_me(client):
    bad = client.post("/api/auth/login", json={"email": "test@naver.com", "password": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "test@naver.com", "password": "12341234"})
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "test@naver.com"
    me = client.get("/api/auth/me", cookies=ok.cookies)
    assert me.status_code == 200


def test_admin_users_list(client):
    login = client.post("/api/auth/login", json={"email": "test@naver.com", "password": "12341234"})
    res = client.get("/api/admin/users", cookies=login.cookies)
    assert res.status_code == 200
    emails = [u["email"] for u in res.json()["items"]]
    assert "test@naver.com" in emails


def test_ui_pages_redirect_to_root_login(client):
    res = client.get("/ui/admin.html", follow_redirects=False)
    assert res.status_code in (302, 303, 307)
    loc = urlparse(res.headers["location"])
    assert loc.path == "/"
    assert parse_qs(loc.query) == {"next": ["/ui/admin.html"]}


def test_legacy_login_page_redirects_home(client):
    res = client.get("/ui/login.html", follow_redirects=False)
    assert res.status_code in (302, 303, 307)
    loc = urlparse(res.headers["location"])
    assert loc.path == "/"
    assert loc.query == ""


def test_root_is_public(client):
    res = client.get("/")
    assert res.status_code == 200
