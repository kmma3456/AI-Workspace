"""수신 -> 필터 -> Claude -> 하드 규칙 -> 발송 -> 기록 파이프라인과 예약 작업들."""

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from app.agent.guardrails import FALLBACK_REPLY, check_reply
from app.agent.prompts import build_system_prompt
from app.agent.runner import NO_REPLY, run_agent, to_claude_messages
from app.agent.tools import ToolExecutor
from app.db import Source
from app.deps import Deps
from app.filters import FilterResult, evaluate_inbound
from app.integrations.sheets import QuoteStatus
from app.missed_call_notice import MissedCallNotice, parse_missed_call_notice
from app.intake_memo_sheet import build_intake_memo_row
from app.missed_call_intake import (
    CATEGORY_LABELS,
    INTAKE_CALENDAR_DELAY,
    INTAKE_EVENT_DURATION,
    INTAKE_RECOVERY_WINDOW,
    MISSED_CALL_MENU_MARKER,
    REPLY_AFTER_CHOICE,
    REPLY_BAD_MENU,
    REPLY_DONE,
    build_calendar_summary,
    dump_intake,
    intake_flag_key,
    intake_phone_variants,
    load_intake,
    new_intake_state,
    parse_menu_choice,
)
from app.runtime_policy import effective_dry_run, effective_max_replies, effective_test_numbers
from app.quiet_hours import (
    build_defer_customer_sms,
    effective_quiet_hours,
    resolve_calendar_start,
)
from app.utils import extract_amounts, format_phone_display, is_korean_mobile, normalize_phone

logger = logging.getLogger(__name__)

PAUSE_FLAG = "paused"
MISSED_CALL_COOLDOWN = timedelta(minutes=10)


def _format_quote_amount(raw: str) -> str:
    digits = (raw or "").replace(",", "").strip()
    if digits.isdigit():
        return f"{int(digits):,}"
    return (raw or "").strip()


def build_quote_outbound_message(quote: dict, business_name: str) -> str:
    """사장님이 입력한 견적금액·내용을 고객 문자로 그대로 전달."""
    service = (quote.get("서비스") or "").strip()
    amount = _format_quote_amount(str(quote.get("견적금액") or ""))
    detail = (quote.get("견적내용") or "").strip()
    parts = [f"안녕하세요, {business_name}입니다."]
    if service:
        parts.append(f"요청하신 {service} 견적 안내드립니다.")
    else:
        parts.append("요청하신 견적 안내드립니다.")
    if amount:
        parts.append(f"견적 금액: {amount}원")
    if detail:
        parts.append(detail)
    parts.append("진행을 원하시면 희망 일정을 문자로 알려 주시면 조율해 드리겠습니다.")
    return "\n".join(parts)


DEFAULT_MISSED_CALL_TEMPLATE = (
    "안녕하십니까, OK모터스·차산후 차대균 대표입니다.\n"
    "현재 현장 정비 및 차량 검진 작업 중으로 즉시 통화 연결이 어렵습니다.\n"
    "작업 확인 후 1시간 이내로 신속히 연락드리겠습니다.\n\n"
    "원활한 상담 및 예약을 위해 문의하시려는 번호를 선택(입력)해 주시면 감사하겠습니다.\n\n"
    "1. 중고차 성능보증 수리 및 점검 예약\n"
    "2. 일반 정비 및 정비 견적 문의\n"
    "3. 지인 / 개인적인 통화 요청"
)

URGENT_OWNER_LABEL = "차대균 대표"
URGENT_CALENDAR_DELAY = timedelta(minutes=1)
URGENT_CALENDAR_BLOCK = timedelta(minutes=15)


def reminder_message(business_name: str, kind: str, start: datetime) -> str:
    return (
        f"안녕하세요, {business_name}입니다. 내일 {start:%m월 %d일 %H:%M} {kind} 일정 안내드립니다. "
        "변경이 필요하시면 이 번호로 문자 주세요."
    )


@dataclass
class TurnOutcome:
    reply: str | None = None
    original_reply: str | None = None
    blocked_reason: str = ""
    category: str = ""
    summary: str = ""
    escalated: bool = False
    trace: list[dict] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ConversationService:
    def __init__(self, deps: Deps):
        self.deps = deps
        self.settings = deps.settings

    def now(self) -> datetime:
        return datetime.now(self.settings.tz)

    def _quiet_hours(self):
        return effective_quiet_hours(self.settings, self.deps.db)

    def _resolve_calendar_start(self, moment: datetime, normal_start: datetime) -> tuple[datetime, bool]:
        return resolve_calendar_start(moment, normal_start, self._quiet_hours(), self.settings.tz)

    # ---------- 수신 판단 ----------

    async def is_paused(self) -> bool:
        if self.deps.db.get_flag(PAUSE_FLAG) == "1":
            return True
        return not await self.deps.sheets.ai_enabled()

    async def should_handle(self, phone: str, text: str, *, check_rate_limit: bool = True) -> FilterResult:
        recent = self.deps.db.count_outgoing_since(phone, self.now() - timedelta(hours=1))
        return evaluate_inbound(
            phone, text,
            paused=await self.is_paused(),
            excluded=await self.deps.sheets.excluded_numbers(),
            test_numbers=effective_test_numbers(self.settings, self.deps.db),
            recent_outgoing=recent,
            max_per_hour=effective_max_replies(self.settings, self.deps.db),
            check_rate_limit=check_rate_limit,
        )

    async def format_missed_call_message(self) -> str:
        tpl = await self.deps.sheets.get_setting("MISSED_CALL_MESSAGE")
        if not tpl:
            tpl = DEFAULT_MISSED_CALL_TEMPLATE
        tpl = tpl.replace("{business_name}", self.settings.business_name)
        qh = self._quiet_hours()
        if qh.active_at(self.now()):
            tpl = tpl.replace(
                "작업 확인 후 1시간 이내로 신속히 연락드리겠습니다.",
                "영업 시작 후 순차적으로 연락드리겠습니다.",
            )
            note = "※ 현재는 응대 시간 외입니다. 접수 확인 후 영업 시작 시 연락드립니다."
            if note not in tpl:
                tpl = f"{tpl.rstrip()}\n\n{note}"
        return tpl

    async def handle_urgent_partner_sms(
        self,
        phone: str,
        text: str,
        *,
        keywords: list[str],
        sender_label: str = "",
    ) -> dict:
        """협회·보험 키워드 문자 — AI 생략, 캘린더에 긴급 일정 등록(시작 1분 후 알림용)."""
        self.deps.db.add_message(phone, Source.CUSTOMER, text)
        await self.deps.sheets.touch_customer(phone, self.now())
        customer = await self.deps.sheets.get_customer(phone)
        customer_name = (customer or {}).get("이름") or sender_label or "-"
        kw_label = ", ".join(keywords)
        phone_disp = format_phone_display(phone)
        now = self.now()
        normal_start = now + URGENT_CALENDAR_DELAY
        start, deferred = self._resolve_calendar_start(now, normal_start)
        end = start + URGENT_CALENDAR_BLOCK
        summary = f"[긴급·협회] {keywords[0]} · {phone_disp}"
        description = (
            f"수신: {URGENT_OWNER_LABEL}\n"
            f"키워드: {kw_label}\n"
            f"고객번호: {phone_disp} ({phone})\n"
            f"고객명/표시: {customer_name}\n"
            f"수신 시각: {now:%Y-%m-%d %H:%M}\n"
            f"{'[야간 접수 → 익일 연락]' if deferred else ''}\n"
            f"---\n{text}"
        )
        event_id = await self.deps.calendar.create_event(
            summary=summary,
            start=start,
            end=end,
            description=description,
            private={"phone": phone, "urgent_keywords": kw_label, "kind": "urgent_partner"},
            popup_minutes_before=0,
        )
        await self.deps.sheets.log_inquiry(
            now,
            phone,
            "문자(긴급·협회)",
            "긴급",
            f"협회 키워드 {kw_label}",
            text,
            f"캘린더 등록 {start:%H:%M} ({event_id[:8]}…)",
            "사장님확인필요",
        )
        if deferred:
            await self.send(phone, build_defer_customer_sms(self.settings.business_name, start), enforce_limit=False)
        logger.info(
            "긴급 협회 문자 캘린더 등록 phone=%s keywords=%s event=%s deferred=%s",
            phone,
            kw_label,
            event_id,
            deferred,
        )
        return {
            "calendar_event_id": event_id,
            "keywords": keywords,
            "calendar_start": start.isoformat(),
            "deferred_quiet_hours": deferred,
        }

    # ---------- 발송 ----------

    async def send(self, phone: str, text: str, source: str = Source.AI, *, enforce_limit: bool = True) -> bool:
        if enforce_limit:
            recent = self.deps.db.count_outgoing_since(phone, self.now() - timedelta(hours=1))
            if recent >= effective_max_replies(self.settings, self.deps.db):
                await self.deps.notifier.notify(f"[발송 차단] 시간당 발송 한도 초과: {phone}\n{text}")
                return False
        if effective_dry_run(self.settings, self.deps.db) and not self.settings.mock_mode:
            await self.deps.notifier.notify(f"[DRY RUN] -> {phone}\n{text}")
        else:
            await self.deps.gateway.send(phone, text)
        self.deps.db.add_message(phone, source, text)
        return True

    # ---------- 대화 ----------

    async def _context(self, phone: str) -> dict:
        sheets = self.deps.sheets
        knowledge = await sheets.knowledge()
        quotes = await sheets.quotes_for(phone)
        allowed = set()
        for row in knowledge:
            allowed |= extract_amounts(row["답변"])
        for quote in quotes:
            allowed |= extract_amounts(quote["견적금액"] + " " + quote["견적내용"])
            if quote["견적금액"].replace(",", "").isdigit():
                allowed.add(int(quote["견적금액"].replace(",", "")))
        return {
            "knowledge": knowledge,
            "customer": await sheets.get_customer(phone),
            "quotes": quotes,
            "appointments": await sheets.appointments_for(phone),
            "allowed_amounts": allowed,
        }

    async def respond(self, phone: str, instruction: str | None = None) -> TurnOutcome:
        """대화 이력 기준으로 답장을 생성하고 하드 규칙을 적용한다 (발송은 하지 않음)."""
        ctx = await self._context(phone)
        customer = ctx["customer"]
        executor = ToolExecutor(phone, self.deps, customer_name=customer["이름"] if customer else "")
        system = build_system_prompt(
            self.settings, self.now(), ctx["knowledge"], customer, ctx["quotes"], ctx["appointments"], NO_REPLY,
        )
        history = self.deps.db.history(phone, self.settings.history_limit)
        if instruction:
            history.append({"role": "user", "content": instruction})
        result = await run_agent(
            self.deps.claude,
            model=self.settings.anthropic_model,
            max_tokens=self.settings.anthropic_max_tokens,
            system=system,
            messages=to_claude_messages(history),
            executor=executor,
        )
        turn = executor.turn
        outcome = TurnOutcome(
            reply=result.reply, original_reply=result.reply, category=turn.category,
            summary=turn.summary, escalated=turn.escalated, trace=turn.trace,
        )
        if result.reply is None:
            return outcome

        guard = check_reply(result.reply, ctx["allowed_amounts"])
        if not guard.ok:
            await self.deps.notifier.notify(
                f"[답장 차단] {guard.reason}\n고객: {phone}\nAI 원래 답장:\n{result.reply}\n\n"
                "대체 문구로 발송합니다. 확인 후 직접 연락해 주세요."
            )
            outcome.reply = FALLBACK_REPLY
            outcome.blocked_reason = guard.reason
            outcome.escalated = True
        return outcome

    async def handle_inbound(self, phone: str, text: str) -> TurnOutcome:
        self.deps.db.add_message(phone, Source.CUSTOMER, text)
        await self.deps.sheets.touch_customer(phone, self.now())
        try:
            outcome = await self.respond(phone)
        except Exception as e:
            logger.exception("AI 응답 생성 실패: %s", phone)
            await self.deps.notifier.notify(f"[AI 오류] {phone} 메시지를 처리하지 못했습니다.\n{text}")
            await self.send(phone, FALLBACK_REPLY)
            await self.deps.sheets.log_inquiry(
                self.now(), phone, "문자", "", f"AI 오류: {type(e).__name__}", text, FALLBACK_REPLY, "사장님확인필요",
            )
            return TurnOutcome(reply=FALLBACK_REPLY, escalated=True, error=f"{type(e).__name__}: {e}")

        if outcome.reply:
            await self.send(phone, outcome.reply)
        await self.deps.sheets.log_inquiry(
            self.now(), phone, "문자", outcome.category, outcome.summary, text, outcome.reply or "(응답 안 함)",
            "사장님확인필요" if outcome.escalated else "AI처리",
        )
        return outcome

    async def should_send_missed_call_sms(self, phone: str) -> FilterResult:
        """부재중 안내 문자는 AI 일시정지와 별개로 보낼 수 있다."""
        if await self.deps.sheets.get_setting("MISSED_CALL_ENABLED") == "0":
            return FilterResult(False, "disabled")
        if not is_korean_mobile(phone):
            return FilterResult(False, "not_mobile")
        if phone in await self.deps.sheets.excluded_numbers():
            return FilterResult(False, "excluded")
        tests = effective_test_numbers(self.settings, self.deps.db)
        if tests and phone not in tests:
            return FilterResult(False, "not_test_number")
        return FilterResult(True)

    async def handle_missed_call(self, phone: str, channel: str = "전화(부재중)") -> bool:
        verdict = await self.should_send_missed_call_sms(phone)
        if not verdict.allowed:
            logger.info("부재중 전화 자동문자 생략 %s: %s", phone, verdict.reason)
            await self.deps.notifier.notify(f"[부재중 문자 생략] {phone}\n사유: {verdict.reason}")
            return False
        last = self.deps.db.last_outgoing_at(phone)
        if last and self.now() - last < MISSED_CALL_COOLDOWN:
            logger.info("부재중 전화 쿨다운 %s", phone)
            await self.deps.notifier.notify(f"[부재중 문자 생략] {phone}\n사유: 10분 이내 이미 발송됨")
            return False
        text = await self.format_missed_call_message()
        if not await self.send(phone, text, Source.TEMPLATE, enforce_limit=False):
            await self.deps.notifier.notify(f"[부재중 문자 실패] {phone}\n발송이 차단되었습니다.")
            return False
        self._start_missed_call_intake(phone)
        await self.deps.sheets.touch_customer(phone, self.now())
        await self.deps.sheets.log_inquiry(self.now(), phone, channel, "", "부재중 전화 자동 문자", "", text, "AI처리")
        return True

    def _start_missed_call_intake(self, phone: str) -> None:
        phone = normalize_phone(phone)
        self.deps.db.set_flag(intake_flag_key(phone), dump_intake(new_intake_state(self.now())))

    def _load_intake_for_phone(self, phone: str) -> tuple[str, dict] | tuple[None, None]:
        canonical = normalize_phone(phone)
        for variant in intake_phone_variants(phone):
            raw = self.deps.db.get_flag(intake_flag_key(variant))
            state = load_intake(raw)
            if not state:
                continue
            if variant != canonical:
                self.deps.db.set_flag(intake_flag_key(variant), "")
                self.deps.db.set_flag(intake_flag_key(canonical), dump_intake(state))
            return canonical, state
        return None, None

    def _recent_missed_call_menu_sent(self, phone: str) -> bool:
        """초기 부재중 안내(1·2·3)를 보낸 직후인지 — intake 플래그가 없을 때 복구."""
        cutoff = self.now() - INTAKE_RECOVERY_WINDOW
        for variant in intake_phone_variants(phone):
            for msg in reversed(self.deps.db.history(variant, 20)):
                if msg.get("source") != Source.TEMPLATE:
                    continue
                created = datetime.fromisoformat(msg["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff.astimezone(timezone.utc):
                    break
                if MISSED_CALL_MENU_MARKER in (msg.get("content") or ""):
                    return True
        return False

    async def try_handle_missed_call_intake(self, phone: str, text: str) -> dict | None:
        """부재중 분기 시나리오 진행 중이면 AI 대신 처리."""
        resolved_phone, state = self._load_intake_for_phone(phone)
        if not state:
            if parse_menu_choice(text) and self._recent_missed_call_menu_sent(phone):
                logger.warning("부재중 intake 플래그 없음 — 최근 안내 문자 기준으로 복구 phone=%s", phone)
                self._start_missed_call_intake(phone)
                return await self.try_handle_missed_call_intake(phone, text)
            return None
        phone = resolved_phone or normalize_phone(phone)

        now = self.now()
        self.deps.db.add_message(phone, Source.CUSTOMER, text)
        state["last_customer_at"] = now.isoformat()
        state.setdefault("lines", []).append(text)

        phase = state.get("phase", "menu")
        if phase == "menu":
            choice = parse_menu_choice(text)
            if not choice:
                await self.send(phone, REPLY_BAD_MENU, Source.AI, enforce_limit=False)
                self.deps.db.set_flag(intake_flag_key(phone), dump_intake(state))
                return {"handled": True, "missed_call_intake": "bad_menu"}
            state["phase"] = "collect"
            state["category"] = choice
            if not await self.send(phone, REPLY_AFTER_CHOICE[choice], Source.AI, enforce_limit=False):
                logger.error("부재중 분기 안내 문자 발송 실패 phone=%s category=%s", phone, choice)
                await self.deps.notifier.notify(
                    f"[발송 실패] 부재중 분기 안내 미전송 · {format_phone_display(phone)}\n선택: {choice}"
                )
            self.deps.db.set_flag(intake_flag_key(phone), dump_intake(state))
            return {"handled": True, "missed_call_intake": "branch_prompt", "category": choice}

        # collect — 마지막 문자 시각 + 1시간에 캘린더 등록
        category = str(state.get("category", "?"))
        last_at = datetime.fromisoformat(state["last_customer_at"])
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=self.settings.tz)
        normal_start = last_at + INTAKE_CALENDAR_DELAY
        start, deferred = self._resolve_calendar_start(now, normal_start)
        end = start + INTAKE_EVENT_DURATION
        phone_disp = format_phone_display(phone)
        summary, description = build_calendar_summary(phone_disp, category, state.get("lines", []))
        event_id = await self.deps.calendar.create_event(
            summary=summary,
            start=start,
            end=end,
            description=description,
            private={"phone": phone, "kind": "missed_call_intake", "category": category},
            popup_minutes_before=0,
        )
        customer = await self.deps.sheets.get_customer(phone)
        customer_name = (customer or {}).get("이름") or ""
        memo_row = build_intake_memo_row(
            category=category,
            phone=phone,
            phone_display=phone_disp,
            customer_name=customer_name,
            lines=state.get("lines", []),
            received_at=last_at,
        )
        await self.deps.sheets.append("접수메모", memo_row)
        if deferred:
            await self.send(phone, build_defer_customer_sms(self.settings.business_name, start), enforce_limit=False)
        else:
            await self.send(phone, REPLY_DONE, Source.AI, enforce_limit=False)
        await self.deps.sheets.log_inquiry(
            now,
            phone,
            "문자(부재중 후속)",
            category,
            CATEGORY_LABELS.get(category, category),
            text,
            f"캘린더 {start:%m/%d %H:%M} ({event_id[:8]}…)"
            + (" · 야간→익일" if deferred else ""),
            "사장님확인필요",
        )
        self.deps.db.set_flag(intake_flag_key(phone), "")
        logger.info(
            "부재중 후속 캘린더 등록 phone=%s category=%s event=%s deferred=%s",
            phone,
            category,
            event_id,
            deferred,
        )
        return {
            "handled": True,
            "missed_call_intake": "scheduled",
            "category": category,
            "calendar_event_id": event_id,
            "calendar_start": start.isoformat(),
            "deferred_quiet_hours": deferred,
        }

    async def handle_missed_call_notice(self, sender: str, text: str) -> MissedCallNotice | None:
        """통신사 부재중 알림 문자면 그 안의 발신자에게 응대를 시작한다. 알림 문자가 아니면 None."""
        notice = parse_missed_call_notice(sender, text, self.settings.missed_call_notice_sender_set)
        if notice is None:
            return None
        for phone in notice.mobile_callers:
            await self.handle_missed_call(phone, channel="전화(통신사 알림)")
        for phone in notice.other_callers:
            await self.deps.sheets.log_inquiry(
                self.now(), phone, "전화(통신사 알림)", "", "유선/기타 번호 부재중 - 문자 응대 불가", text, "", "사장님확인필요",
            )
            await self.deps.notifier.notify(f"[부재중 전화] {phone}\n휴대폰 번호가 아니라 문자 응대를 못 했습니다. 직접 전화해 주세요.")
        return notice

    # ---------- 사장님 조작 ----------

    async def owner_reply(self, phone: str, text: str) -> None:
        await self.send(phone, text, Source.OWNER, enforce_limit=False)
        await self.deps.sheets.log_inquiry(self.now(), phone, "문자", "", "사장님 직접 답장", "", text, "사장님처리")

    async def mark_quote_ready(self, quote_id: str, amount: str, content: str) -> bool:
        updated = await self.deps.sheets.set_quote_status(quote_id, QuoteStatus.READY, 견적금액=amount, 견적내용=content)
        if updated:
            await self.send_ready_quotes()
        return updated

    # ---------- 예약 작업 ----------

    async def send_ready_quotes(self) -> None:
        """사장님이 입력한 견적금액·내용을 고객에게 문자로 발송."""
        if await self.is_paused():
            return
        for quote in await self.deps.sheets.quotes_by_status(QuoteStatus.READY):
            phone = quote["전화번호"]
            text = build_quote_outbound_message(quote, self.settings.business_name)
            try:
                await self.send(phone, text, Source.OWNER, enforce_limit=False)
            except Exception:
                logger.exception("견적 문자 발송 실패: %s", quote["견적ID"])
                await self.deps.notifier.notify(f"[견적 발송 실패] {quote['견적ID']} · {phone}\n직접 안내가 필요합니다.")
                continue
            if not self.deps.db.mark_processed(f"quote:{quote['견적ID']}"):
                logger.warning("견적 중복 발송 방지: %s", quote["견적ID"])
                continue
            await self.deps.sheets.log_inquiry(
                self.now(),
                phone,
                "문자",
                "견적안내",
                f"견적 {quote['견적ID']} 발송",
                "",
                text,
                "사장님처리",
            )
            await self.deps.sheets.set_quote_status(
                quote["견적ID"], QuoteStatus.SENT, 발송일=self.now().strftime("%Y-%m-%d %H:%M"),
            )

    async def send_reminders(self) -> None:
        if await self.is_paused():
            return
        tomorrow = (self.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        events = await self.deps.calendar.events_between(tomorrow, tomorrow + timedelta(days=1))
        for event in events:
            private = event.get("extendedProperties", {}).get("private", {})
            phone = private.get("phone")
            if not phone or not self.deps.db.mark_processed(f"reminder:{event['id']}"):
                continue
            start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(self.settings.tz)
            await self.send(phone, reminder_message(self.settings.business_name, private.get("type", "방문"), start),
                            Source.TEMPLATE)

    def today_stats(self) -> dict:
        start_of_day = self.now().replace(hour=0, minute=0, second=0, microsecond=0)
        stats = self.deps.db.message_stats_since(start_of_day)
        empty = {"messages": 0, "customers": 0}
        yesterday_start = start_of_day - timedelta(days=1)
        y_stats = self.deps.db.message_stats_between(yesterday_start, start_of_day)
        y_inbound = y_stats.get(Source.CUSTOMER, empty)["messages"]
        t_inbound = stats.get(Source.CUSTOMER, empty)["messages"]
        return {
            "date": start_of_day.strftime("%Y-%m-%d"),
            "inbound": stats.get(Source.CUSTOMER, empty),
            "ai": stats.get(Source.AI, empty),
            "owner": stats.get(Source.OWNER, empty),
            "template": stats.get(Source.TEMPLATE, empty),
            "inbound_vs_yesterday": t_inbound - y_inbound,
        }

    async def daily_report(self) -> None:
        stats = self.today_stats()
        waiting = await self.deps.sheets.quotes_by_status(QuoteStatus.WAITING)
        lines = [
            f"[일일 리포트] {stats['date']}",
            f"- 받은 메시지: {stats['inbound']['messages']}건 ({stats['inbound']['customers']}명)",
            f"- AI 답장: {stats['ai']['messages']}건",
            f"- 견적 작성 대기: {len(waiting)}건",
        ]
        lines += [f"  · {q['견적ID']} {q['이름'] or q['전화번호']} - {q['서비스']}" for q in waiting[:10]]
        await self.deps.notifier.notify("\n".join(lines))
