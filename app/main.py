import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.batching import MessageBatcher
from app.config import get_settings
from app.deps import build_deps
from app.integrations.sheets import GoogleSheetsRepo
from app.scheduler import build_scheduler
from app.runtime_policy import apply_policy, effective_batch_window, policy_snapshot
from app.quiet_hours import apply_quiet_hours, quiet_hours_snapshot
from app.intake_memo_sheet import INTAKE_MEMO_STATUS_OPTIONS
from app.service import DEFAULT_MISSED_CALL_TEMPLATE, PAUSE_FLAG, ConversationService
from app.urgent_keywords import find_urgent_partner_keywords
from app.missed_call_intake import parse_menu_choice
from app.admin_auth import SESSION_COOKIE, SESSION_HEADER, AdminAuth, AdminUser
from app.utils import normalize_phone

import sqlite3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_DIR = Path(__file__).parent / "public"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    deps = build_deps(settings)
    if isinstance(deps.sheets, GoogleSheetsRepo):
        created = deps.sheets.ensure_tabs()
        if created:
            logger.info("구글 시트 탭 생성: %s", ", ".join(created))
    service = ConversationService(deps)
    batcher = MessageBatcher(settings.batch_window_seconds, service.handle_inbound)
    scheduler = build_scheduler(service, settings)
    scheduler.start()

    app.state.service = service
    app.state.batcher = batcher
    auth = AdminAuth(deps.db)
    boot = auth.ensure_user("test@naver.com", "12341234")
    if boot:
        logger.info("관리자 계정 생성: %s", boot.email)
    logger.info("AI 직원 시작 (mock_mode=%s, dry_run=%s)", settings.mock_mode, settings.dry_run)
    yield
    scheduler.shutdown(wait=False)
    await batcher.flush_all()
    await deps.aclose()


app = FastAPI(title="AI 직원", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
if PUBLIC_DIR.is_dir():
    app.mount("/public", StaticFiles(directory=PUBLIC_DIR), name="public")

LOCAL_ONLY_PREFIXES = ("/ui", "/api", "/public", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def ui_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/ui/") and (path.endswith(".html") or path.endswith(".css") or path.endswith(".js")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


def _root_login_url(next_path: str = "") -> str:
    if next_path.startswith("/ui/") and not next_path.startswith("//"):
        return f"/?next={quote(next_path, safe='')}"
    return "/"


@app.middleware("http")
async def protect_admin_ui(request: Request, call_next):
    path = request.url.path
    if path in ("/ui/login.html", "/ui/index.html"):
        return RedirectResponse(_root_login_url(request.query_params.get("next", "")))
    if path.startswith("/ui/") and path.endswith(".html"):
        service = getattr(request.app.state, "service", None)
        if service is not None:
            token = request.cookies.get(SESSION_COOKIE) or request.headers.get(SESSION_HEADER, "")
            if not AdminAuth(service.deps.db).resolve_token(token):
                return RedirectResponse(_root_login_url(path))
    return await call_next(request)


@app.middleware("http")
async def block_admin_through_tunnel(request: Request, call_next):
    """Cloudflare 터널로 들어온 요청은 웹훅만 허용하고, 관리 화면/API는 이 컴퓨터에서만 열리게 한다."""
    if "cf-connecting-ip" in request.headers and request.url.path.startswith(LOCAL_ONLY_PREFIXES):
        return JSONResponse({"detail": "local only"}, status_code=403)
    return await call_next(request)


def _token_ok(value: str) -> bool:
    return bool(value) and hmac.compare_digest(value, get_settings().webhook_token)


def require_token(x_webhook_token: str = Header(default="")) -> None:
    if not _token_ok(x_webhook_token):
        raise HTTPException(status_code=401, detail="invalid token")


def require_phone_token(request: Request) -> None:
    """MacroDroid는 헤더 또는 주소의 ?token= 으로 토큰을 보낸다."""
    if not _token_ok(request.headers.get("x-webhook-token") or request.query_params.get("token", "")):
        raise HTTPException(status_code=401, detail="invalid token")


def _session_token(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE) or request.headers.get(SESSION_HEADER, "")


def get_admin_auth(request: Request) -> AdminAuth:
    return AdminAuth(get_service(request).deps.db)


def require_admin(request: Request) -> AdminUser:
    user = get_admin_auth(request).resolve_token(_session_token(request))
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


def get_service(request: Request) -> ConversationService:
    return request.app.state.service


class PhoneIn(BaseModel):
    phoneNumber: str


class SimulateIn(BaseModel):
    phoneNumber: str
    message: str
    applyFilters: bool = False


class TextIn(BaseModel):
    text: str


class QuoteReadyIn(BaseModel):
    amount: str
    content: str


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


# ---------- 외부 웹훅 (휴대폰) ----------

@app.post("/webhooks/sms")
async def sms_webhook(
    request: Request,
    x_signature: str | None = Header(default=None),
    x_timestamp: str | None = Header(default=None),
) -> dict:
    service = get_service(request)
    body = await request.body()
    if not service.deps.gateway.verify_signature(body, x_timestamp, x_signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    data = json.loads(body)
    if data.get("event") != "sms:received":
        return {"ignored": data.get("event")}

    payload = data.get("payload", {})
    phone = normalize_phone(payload.get("phoneNumber", ""))
    text = (payload.get("message") or "").strip()
    event_key = data.get("id") or payload.get("messageId") or hashlib.sha1(
        f"{phone}|{text}|{payload.get('receivedAt')}".encode()
    ).hexdigest()
    return await ingest_sms(request, phone, text, event_key)


async def ingest_sms(
    request: Request, phone: str, text: str, event_key: str, *, sender_label: str = ""
) -> dict:
    service = get_service(request)
    if not service.deps.db.mark_processed(f"sms:{event_key}"):
        return {"duplicate": True}

    try:
        notice = await service.handle_missed_call_notice(phone, text)
        if notice is not None:
            return {"missed_call_notice": notice.mobile_callers + notice.other_callers}

        keywords = find_urgent_partner_keywords(text, sender_label)
        if keywords:
            urgent = await service.handle_urgent_partner_sms(
                phone, text, keywords=keywords, sender_label=sender_label,
            )
            return {"handled": True, "urgent_partner": True, **urgent}

        intake = await service.try_handle_missed_call_intake(phone, text)
        if intake is not None:
            return intake
        if parse_menu_choice(text):
            logger.warning(
                "부재중 메뉴 번호 수신했으나 intake 미처리 phone=%s text=%r (번호·웹훅 확인)",
                phone,
                text,
            )

        verdict = await service.should_handle(phone, text)
    except Exception:
        logger.exception("수신 문자 처리 실패: %s", phone)
        await service.deps.notifier.notify(f"[처리 실패] 문자를 AI가 처리하지 못했습니다. 직접 확인해 주세요.\n{phone}: {text}")
        return {"handled": False, "reason": "error"}
    if not verdict.allowed:
        logger.info("응답 안 함 %s: %s", phone, verdict.reason)
        return {"handled": False, "reason": verdict.reason}

    request.app.state.batcher.add(phone, text)
    return {"handled": True}


async def _phone_params(request: Request) -> dict:
    """MacroDroid HTTP 요청은 주소 파라미터, 폼, JSON 중 무엇으로 보내도 받는다."""
    params = dict(request.query_params)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            try:
                params.update(await request.json())
            except ValueError:
                pass
        elif "form" in content_type:
            params.update(dict(await request.form()))
    return params


@app.api_route("/webhooks/macrodroid/sms", methods=["GET", "POST"], dependencies=[Depends(require_phone_token)])
async def macrodroid_sms(request: Request) -> dict:
    params = await _phone_params(request)
    phone = _macrodroid_inbound_phone(params)
    text = str(params.get("text", "") or params.get("message", "") or params.get("body", "")).strip()
    sender_label = _macrodroid_sender_label(params)
    if not phone:
        logger.warning("MacroDroid SMS 웹훅: 발신 번호 없음 params=%s", {k: params.get(k) for k in params})
    minute = datetime.now().strftime("%Y%m%d%H%M")
    event_key = hashlib.sha1(f"{phone}|{text}|{minute}".encode()).hexdigest()
    return await ingest_sms(request, phone, text, event_key, sender_label=sender_label)


def _macrodroid_inbound_phone(params: dict) -> str:
    for key in (
        "phone",
        "number",
        "from",
        "sender",
        "sms_from",
        "sms_number",
        "originating_address",
        "sms_from_num",
    ):
        raw = str(params.get(key, "")).strip()
        if raw:
            phone = normalize_phone(raw)
            if phone:
                return phone
    return ""


def _macrodroid_sender_label(params: dict) -> str:
    for key in ("contact", "name", "sender", "from_name", "caller"):
        value = str(params.get(key, "")).strip()
        if value:
            return value
    return ""


@app.api_route("/webhooks/macrodroid/missed-call", methods=["GET", "POST"], dependencies=[Depends(require_phone_token)])
async def macrodroid_missed_call(request: Request) -> dict:
    params = await _phone_params(request)
    phone = normalize_phone(str(params.get("phone", "")))
    if not phone:
        return {"accepted": False, "reason": "missing_phone"}
    sent = await get_service(request).handle_missed_call(phone)
    return {"accepted": True, "sent": sent}


@app.post("/webhooks/missed-call", dependencies=[Depends(require_token)])
async def missed_call_webhook(body: PhoneIn, request: Request) -> dict:
    phone = normalize_phone(body.phoneNumber)
    sent = await get_service(request).handle_missed_call(phone)
    return {"accepted": True, "sent": sent}


# ---------- 화면용 API ----------

auth_api = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    email: str
    password: str


@auth_api.post("/login")
async def auth_login(body: LoginIn, request: Request, response: Response) -> dict:
    auth = get_admin_auth(request)
    user = auth.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = auth.create_session(user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"ok": True, "user": auth.user_public(user)}


@auth_api.post("/logout")
async def auth_logout(request: Request, response: Response) -> dict:
    get_admin_auth(request).revoke_token(_session_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@auth_api.get("/me")
async def auth_me(admin: AdminUser = Depends(require_admin)) -> dict:
    return {"user": {"id": admin.id, "email": admin.email}}


api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


class AdminUserCreateIn(BaseModel):
    email: str
    password: str


class AdminUserPatchIn(BaseModel):
    disabled: bool | None = None
    password: str | None = None


@api.get("/admin/users")
async def admin_list_users(request: Request, _admin: AdminUser = Depends(require_admin)) -> dict:
    auth = get_admin_auth(request)
    return {"items": [auth.user_public(u) for u in auth.list_users()]}


@api.post("/admin/users")
async def admin_create_user(body: AdminUserCreateIn, request: Request, _admin: AdminUser = Depends(require_admin)) -> dict:
    auth = get_admin_auth(request)
    try:
        created = auth.create_user(body.email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="email already exists") from e
    return {"user": auth.user_public(created)}


@api.patch("/admin/users/{user_id}")
async def admin_patch_user(
    user_id: int,
    body: AdminUserPatchIn,
    request: Request,
    _admin: AdminUser = Depends(require_admin),
) -> dict:
    auth = get_admin_auth(request)
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="not found")
    if body.disabled is not None:
        if body.disabled and auth.count_users() <= 1:
            raise HTTPException(status_code=400, detail="cannot disable only admin")
        auth.set_disabled(user_id, body.disabled)
    if body.password:
        try:
            auth.set_password(user_id, body.password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    updated = auth.get_user_by_id(user_id)
    return {"user": auth.user_public(updated)}


@api.get("/status")
async def status(request: Request) -> dict:
    service = get_service(request)
    s = service.settings
    snap = policy_snapshot(s, service.deps.db)
    intake_rows = await service.deps.sheets.records("접수메모")
    intake_pending = sum(1 for r in intake_rows if "미연락" in (r.get("조치상태") or ""))
    return {
        "business_name": s.business_name,
        "model": s.anthropic_model,
        "mock_mode": s.mock_mode,
        "dry_run": snap["dry_run"],
        "paused": await service.is_paused(),
        "test_numbers": snap["test_numbers"],
        "stats": service.today_stats(),
        "intake_pending": intake_pending,
    }


@api.post("/pause")
async def pause(request: Request) -> dict:
    get_service(request).deps.db.set_flag(PAUSE_FLAG, "1")
    return {"paused": True}


@api.post("/resume")
async def resume(request: Request) -> dict:
    get_service(request).deps.db.set_flag(PAUSE_FLAG, "0")
    return {"paused": await get_service(request).is_paused()}


@api.get("/conversations")
async def conversations(request: Request) -> list[dict]:
    service = get_service(request)
    rows = service.deps.db.conversations()
    customers = {normalize_phone(c["전화번호"]): c for c in await service.deps.sheets.records("고객")}
    for row in rows:
        row["name"] = customers.get(row["phone"], {}).get("이름", "")
    return rows


@api.get("/conversations/{phone}")
async def conversation(phone: str, request: Request) -> dict:
    service = get_service(request)
    phone = normalize_phone(phone)
    return {
        "phone": phone,
        "customer": await service.deps.sheets.get_customer(phone),
        "messages": service.deps.db.history(phone, 500),
        "quotes": await service.deps.sheets.quotes_for(phone),
        "appointments": await service.deps.sheets.appointments_for(phone),
    }


@api.delete("/conversations/{phone}")
async def reset_conversation(phone: str, request: Request) -> dict:
    get_service(request).deps.db.delete_conversation(normalize_phone(phone))
    return {"deleted": True}


@api.post("/conversations/{phone}/reply")
async def owner_reply(phone: str, body: TextIn, request: Request) -> dict:
    await get_service(request).owner_reply(normalize_phone(phone), body.text.strip())
    return {"sent": True}


@api.post("/simulate")
async def simulate(body: SimulateIn, request: Request) -> dict:
    """휴대폰 없이 고객 문자를 흉내 낸다. 묶음 처리는 건너뛰고 바로 응답한다."""
    service = get_service(request)
    phone = normalize_phone(body.phoneNumber)
    if body.applyFilters:
        verdict = await service.should_handle(phone, body.message)
        if not verdict.allowed:
            return {"filtered": verdict.reason}
    outcome = await service.handle_inbound(phone, body.message.strip())
    return outcome.to_dict()


@api.post("/simulate/missed-call")
async def simulate_missed_call(body: PhoneIn, request: Request) -> dict:
    sent = await get_service(request).handle_missed_call(normalize_phone(body.phoneNumber))
    return {"sent": sent}


class NoticeIn(BaseModel):
    sender: str
    message: str


@api.post("/simulate/carrier-notice")
async def simulate_carrier_notice(body: NoticeIn, request: Request) -> dict:
    """통신사 부재중 알림 문자가 휴대폰에 도착한 상황을 흉내 낸다."""
    notice = await get_service(request).handle_missed_call_notice(body.sender, body.message)
    if notice is None:
        return {"recognized": False}
    return {"recognized": True, "mobile": notice.mobile_callers, "other": notice.other_callers}


@api.get("/quotes")
async def quotes(request: Request) -> list[dict]:
    rows = await get_service(request).deps.sheets.open_quotes()
    return sorted(rows, key=lambda r: r["요청일"], reverse=True)


@api.post("/quotes/{quote_id}/ready")
async def quote_ready(quote_id: str, body: QuoteReadyIn, request: Request) -> dict:
    if not await get_service(request).mark_quote_ready(quote_id, body.amount.strip(), body.content.strip()):
        raise HTTPException(status_code=404, detail="견적을 찾을 수 없습니다")
    return {"ok": True}


@api.get("/calendar")
async def calendar(request: Request, days: int = 14) -> list[dict]:
    service = get_service(request)
    start = service.now().replace(hour=0, minute=0, second=0, microsecond=0)
    events = await service.deps.calendar.events_between(start, start + timedelta(days=days))
    result = []
    for e in events:
        if "dateTime" not in e.get("start", {}):
            continue
        private = e.get("extendedProperties", {}).get("private", {})
        result.append({
            "id": e["id"],
            "summary": e.get("summary", ""),
            "start": datetime.fromisoformat(e["start"]["dateTime"]).astimezone(service.settings.tz).strftime("%Y-%m-%d %H:%M"),
            "end": datetime.fromisoformat(e["end"]["dateTime"]).astimezone(service.settings.tz).strftime("%H:%M"),
            "phone": private.get("phone", ""),
            "type": private.get("type", ""),
        })
    return result


@api.get("/notifications")
async def notifications(request: Request) -> list[dict]:
    return list(get_service(request).deps.notifier.recent)


def _parse_activity_day(value: str | None, *, end: bool, tz) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        day = date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="날짜 형식은 YYYY-MM-DD 입니다") from exc
    bound = time.max if end else time.min
    return datetime.combine(day, bound, tz)


@api.get("/activity")
async def activity(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    hours: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    q: str | None = None,
) -> dict:
    """관리 화면 활동 타임라인 — 검색·필터·페이지네이션."""
    db = get_service(request).deps.db
    tz = get_service(request).settings.tz
    since = _parse_activity_day(date_from, end=False, tz=tz)
    until = _parse_activity_day(date_to, end=True, tz=tz)
    if since is None and hours is not None and hours > 0:
        since = datetime.now(timezone.utc) - timedelta(hours=min(hours, 24 * 365))
    allowed = {"customer", "ai", "owner", "template"}
    src = source if source in allowed else None
    query = (q or "").strip() or None
    return db.search_messages(limit=limit, offset=offset, since=since, until=until, source=src, q=query)


@api.get("/knowledge")
async def knowledge(request: Request) -> list[dict]:
    return await get_service(request).deps.sheets.knowledge()


@api.get("/intake-memos")
async def intake_memos(
    request: Request,
    limit: int = 12,
    offset: int = 0,
    pending_only: bool = False,
) -> dict:
    limit = max(1, min(limit, 48))
    offset = max(0, offset)
    rows = list(await get_service(request).deps.sheets.records("접수메모"))
    rows.reverse()
    if pending_only:
        rows = [r for r in rows if "미연락" in (r.get("조치상태") or "")]
    total = len(rows)
    page = rows[offset : offset + limit]
    s = get_settings()
    sheet_url = f"https://docs.google.com/spreadsheets/d/{s.google_sheet_id}/edit" if s.google_sheet_id else ""
    items = []
    for row in page:
        item = {key: row.get(key, "") for key in row if not str(key).startswith("_")}
        item["sheet_row"] = row.get("_row")
        items.append(item)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_options": list(INTAKE_MEMO_STATUS_OPTIONS),
        "sheet_url": sheet_url,
    }


class IntakeMemoStatusIn(BaseModel):
    status: str


@api.patch("/intake-memos/{sheet_row}/status")
async def intake_memo_status(sheet_row: int, body: IntakeMemoStatusIn, request: Request) -> dict:
    if sheet_row < 2:
        raise HTTPException(status_code=400, detail="invalid row")
    status = body.status.strip()
    if status not in INTAKE_MEMO_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="invalid status")
    ok = await get_service(request).deps.sheets.update_row_at("접수메모", sheet_row, {"조치상태": status})
    if not ok:
        raise HTTPException(status_code=404, detail="memo not found")
    return {"ok": True, "status": status}


class MessageSettingsIn(BaseModel):
    missed_call_message: str | None = None
    missed_call_enabled: bool | None = None


@api.get("/settings/messages")
async def get_message_settings(request: Request) -> dict:
    service = get_service(request)
    stored = await service.deps.sheets.get_setting("MISSED_CALL_MESSAGE")
    enabled_raw = await service.deps.sheets.get_setting("MISSED_CALL_ENABLED")
    return {
        "missed_call_message": stored or DEFAULT_MISSED_CALL_TEMPLATE,
        "default_missed_call_message": DEFAULT_MISSED_CALL_TEMPLATE,
        "placeholder_hint": "{business_name}에 업체명이 들어갑니다.",
        "missed_call_enabled": enabled_raw != "0",
    }


@api.get("/settings/integrations")
async def get_integrations(request: Request) -> dict:
    service = get_service(request)
    s = service.settings
    db = service.deps.db

    async def sheets_check() -> dict:
        if s.mock_mode:
            return {"ok": True, "label": "구글 시트", "detail": "MOCK 모드 (메모리)"}
        if not s.google_sheet_id:
            return {"ok": False, "label": "구글 시트", "detail": "GOOGLE_SHEET_ID 미설정"}
        try:
            n = len(await service.deps.sheets.knowledge())
            return {"ok": True, "label": "구글 시트", "detail": f"연결됨 · 지식베이스 {n}건"}
        except Exception as e:
            return {"ok": False, "label": "구글 시트", "detail": str(e)[:180]}

    async def calendar_check() -> dict:
        if s.mock_mode:
            return {"ok": True, "label": "구글 캘린더", "detail": "MOCK 모드 (메모리)"}
        if not s.google_calendar_id:
            return {"ok": False, "label": "구글 캘린더", "detail": "GOOGLE_CALENDAR_ID 미설정"}
        try:
            start = service.now().replace(hour=0, minute=0, second=0, microsecond=0)
            events = await service.deps.calendar.events_between(start, start + timedelta(days=1))
            return {"ok": True, "label": "구글 캘린더", "detail": f"연동 중 · 오늘 일정 {len(events)}건"}
        except Exception as e:
            return {"ok": False, "label": "구글 캘린더", "detail": str(e)[:180]}

    def sms_check() -> dict:
        if s.mock_mode:
            return {"ok": True, "label": "문자 발송", "detail": "MOCK 모드 (실발송 없음)"}
        if s.sms_provider == "macrodroid":
            if s.macrodroid_send_url.strip():
                return {"ok": True, "label": "MacroDroid", "detail": "발송 URL 설정됨"}
            return {"ok": False, "label": "MacroDroid", "detail": "MACRODROID_SEND_URL 비어 있음"}
        if s.sms_gateway_username:
            return {"ok": True, "label": "SMS Gateway", "detail": s.sms_gateway_base_url}
        return {"ok": False, "label": "문자 발송", "detail": "SMS 게이트웨이 계정 미설정"}

    def claude_check() -> dict:
        key = (s.anthropic_api_key or "").strip()
        if not key or "여기에" in key or key.endswith("..."):
            return {"ok": False, "label": "Claude API", "detail": "ANTHROPIC_API_KEY 미설정"}
        masked = key[:12] + "…" if len(key) > 12 else "(설정됨)"
        return {"ok": True, "label": "Claude API", "detail": f"{s.anthropic_model} · {masked}"}

    def telegram_check() -> dict:
        if s.telegram_bot_token and s.telegram_chat_id:
            return {"ok": True, "label": "텔레그램 알림", "detail": "봇·채팅 ID 설정됨"}
        return {"ok": False, "label": "텔레그램 알림", "detail": "미설정 (선택)"}

    recent = db.search_messages(limit=1, offset=0)["items"]
    last_at = recent[0]["created_at"] if recent else None

    return {
        "mock_mode": s.mock_mode,
        "paused": await service.is_paused(),
        "last_message_at": last_at,
        "batch_window_active_seconds": effective_batch_window(s, db),
        "items": [
            claude_check(),
            await sheets_check(),
            await calendar_check(),
            sms_check(),
            telegram_check(),
            {
                "ok": bool(s.webhook_token and s.webhook_token != "change-me-long-random-string"),
                "label": "웹훅 토큰",
                "detail": "MacroDroid·관리 API 인증",
            },
        ],
    }


class PolicyIn(BaseModel):
    dry_run: bool
    test_numbers: str = ""
    max_replies_per_hour: int = 6
    batch_window_seconds: int = 45
    clear_test_numbers: bool = False


class QuietHoursIn(BaseModel):
    enabled: bool
    start: str = "00:00"
    end: str = "09:00"


@api.get("/settings/quiet-hours")
async def get_quiet_hours_settings(request: Request) -> dict:
    service = get_service(request)
    return quiet_hours_snapshot(service.settings, service.deps.db)


@api.put("/settings/quiet-hours")
async def put_quiet_hours_settings(body: QuietHoursIn, request: Request) -> dict:
    db = get_service(request).deps.db
    apply_quiet_hours(db, enabled=body.enabled, start=body.start.strip(), end=body.end.strip())
    service = get_service(request)
    return {"ok": True, "quiet_hours": quiet_hours_snapshot(service.settings, db)}


@api.get("/settings/policy")
async def get_policy_settings(request: Request) -> dict:
    service = get_service(request)
    return policy_snapshot(service.settings, service.deps.db)


@api.put("/settings/policy")
async def put_policy_settings(body: PolicyIn, request: Request) -> dict:
    if body.max_replies_per_hour < 1 or body.max_replies_per_hour > 30:
        raise HTTPException(status_code=400, detail="시간당 발송 한도는 1~30")
    if body.batch_window_seconds < 5 or body.batch_window_seconds > 300:
        raise HTTPException(status_code=400, detail="묶음 대기는 5~300초")
    db = get_service(request).deps.db
    apply_policy(
        db,
        dry_run=body.dry_run,
        test_numbers=body.test_numbers if not body.clear_test_numbers else "",
        max_replies_per_hour=body.max_replies_per_hour,
        batch_window_seconds=body.batch_window_seconds,
        reset_test_numbers=body.clear_test_numbers,
    )
    return {"ok": True, "policy": policy_snapshot(get_service(request).settings, db)}


@api.put("/settings/messages")
async def put_message_settings(body: MessageSettingsIn, request: Request) -> dict:
    service = get_service(request)
    if body.missed_call_enabled is not None:
        await service.deps.sheets.set_setting(
            "MISSED_CALL_ENABLED", "1" if body.missed_call_enabled else "0"
        )
    if body.missed_call_message is not None:
        text = body.missed_call_message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="문구가 비어 있습니다")
        if len(text) > 500:
            raise HTTPException(status_code=400, detail="500자 이내로 작성해 주세요")
        await service.deps.sheets.set_setting("MISSED_CALL_MESSAGE", text)
    return {"ok": True}


@api.post("/jobs/{job}")
async def run_job(job: str, request: Request) -> dict:
    service = get_service(request)
    jobs = {"ready-quotes": service.send_ready_quotes, "reminders": service.send_reminders, "report": service.daily_report}
    if job not in jobs:
        raise HTTPException(status_code=404, detail="unknown job")
    await jobs[job]()
    return {"ok": True}


app.include_router(auth_api)
app.include_router(api)
