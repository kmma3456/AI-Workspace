"""Claude에게 주는 도구와 실제 실행부.

고객 전화번호는 모델 입력으로 받지 않고 ToolExecutor에 고정한다.
모델이 다른 고객의 데이터를 읽거나 바꿀 수 없게 하기 위함.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.deps import Deps
from app.integrations.sheets import AppointmentStatus, QuoteStatus

logger = logging.getLogger(__name__)

INQUIRY_CATEGORIES = ["견적문의", "일정예약", "일정변경", "일정취소", "일반문의", "불만", "기타"]
APPOINTMENT_TYPES = ["상담", "방문견적", "작업"]

TOOLS: list[dict] = [
    {
        "name": "classify_inquiry",
        "description": "이번 고객 메시지의 분류와 한 줄 요약을 기록한다. 매 응답마다 반드시 한 번 호출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": INQUIRY_CATEGORIES},
                "summary": {"type": "string", "description": "사장님이 보기 위한 한 줄 요약"},
            },
            "required": ["category", "summary"],
        },
    },
    {
        "name": "save_customer_info",
        "description": "고객이 이름이나 기억해 둘 정보(주소, 선호사항 등)를 알려줬을 때 고객 시트에 저장한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "memo": {"type": "string"},
            },
        },
    },
    {
        "name": "create_quote_request",
        "description": (
            "견적에 필요한 정보가 충분히 모였을 때 견적 요청을 접수하고 사장님에게 알린다. "
            "금액은 사장님이 정하므로 절대 직접 금액을 말하지 않는다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "요청 서비스 종류"},
                "details": {"type": "string", "description": "규모, 수량, 상태 등 견적에 필요한 상세 내용"},
                "location": {"type": "string", "description": "작업/방문 장소 (해당 시)"},
                "preferred_schedule": {"type": "string", "description": "고객 희망 일정 (자유 형식)"},
            },
            "required": ["service", "details"],
        },
    },
    {
        "name": "find_available_slots",
        "description": "캘린더에서 예약 가능한 시간을 찾는다. 고객에게 시간을 제안하기 전에 반드시 호출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD, 최대 14일 범위"},
                "duration_minutes": {"type": "integer"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "book_appointment",
        "description": "고객이 특정 시간을 명확히 확정했을 때만 일정을 등록한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM (현지 시간)"},
                "duration_minutes": {"type": "integer"},
                "appointment_type": {"type": "string", "enum": APPOINTMENT_TYPES},
                "quote_id": {"type": "string", "description": "관련 견적ID (있으면)"},
                "notes": {"type": "string"},
            },
            "required": ["start", "appointment_type"],
        },
    },
    {
        "name": "reschedule_appointment",
        "description": "이 고객의 기존 일정을 새 시간으로 옮긴다. 새 시간이 비어 있는지 먼저 확인한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "string"},
                "new_start": {"type": "string", "description": "YYYY-MM-DDTHH:MM (현지 시간)"},
            },
            "required": ["appointment_id", "new_start"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "고객이 명확히 취소를 요청했을 때 이 고객의 일정을 취소한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "escalate_to_owner",
        "description": (
            "AI가 처리하면 안 되거나 확신이 없는 건을 사장님에게 넘긴다. "
            "불만, 환불/보상, 가격 협상, 지식베이스에 없는 질문, 고객이 사람을 찾는 경우 등."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "summary": {"type": "string", "description": "사장님이 바로 연락할 수 있도록 상황 요약"},
                "urgency": {"type": "string", "enum": ["normal", "urgent"]},
            },
            "required": ["reason", "summary"],
        },
    },
]


class ToolError(Exception):
    pass


@dataclass
class TurnRecord:
    category: str = ""
    summary: str = ""
    escalated: bool = False
    trace: list[dict] = field(default_factory=list)


class ToolExecutor:
    def __init__(self, phone: str, deps: Deps, customer_name: str = ""):
        self.phone = phone
        self.deps = deps
        self.customer_name = customer_name
        self.turn = TurnRecord()
        self._tz = deps.settings.tz

    async def execute(self, name: str, args: dict) -> tuple[dict, bool]:
        result, is_error = await self._dispatch(name, args)
        self.turn.trace.append({"tool": name, "input": args, "output": result, "is_error": is_error})
        return result, is_error

    async def _dispatch(self, name: str, args: dict) -> tuple[dict, bool]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"알 수 없는 도구: {name}"}, True
        try:
            return await handler(**args), False
        except ToolError as e:
            return {"error": str(e)}, True
        except Exception:
            logger.exception("도구 실행 실패: %s %s", name, args)
            return {"error": "내부 오류. 고객에게는 담당자가 확인 후 연락드린다고 안내할 것."}, True

    # ---------- helpers ----------

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _parse_local(self, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as e:
            raise ToolError(f"시간 형식 오류: {value} (YYYY-MM-DDTHH:MM)") from e
        return parsed.replace(tzinfo=self._tz) if parsed.tzinfo is None else parsed.astimezone(self._tz)

    async def _own_appointment(self, appointment_id: str) -> dict:
        for appt in await self.deps.sheets.appointments_for(self.phone):
            if appt["일정ID"] == appointment_id:
                return appt
        raise ToolError("이 고객의 확정된 일정이 아닙니다.")

    async def _check_slot(self, start: datetime, end: datetime) -> None:
        cal = self.deps.calendar
        if start < self._now():
            raise ToolError("과거 시간은 예약할 수 없습니다.")
        if not cal.within_business_hours(start, end):
            raise ToolError("영업시간 밖입니다. find_available_slots로 가능한 시간을 다시 찾으세요.")
        if not await cal.is_free(start, end):
            raise ToolError("이미 다른 일정이 있는 시간입니다. find_available_slots로 다시 찾으세요.")

    # ---------- tools ----------

    async def _tool_classify_inquiry(self, category: str, summary: str) -> dict:
        self.turn.category = category
        self.turn.summary = summary
        return {"ok": True}

    async def _tool_save_customer_info(self, name: str = "", memo: str = "") -> dict:
        await self.deps.sheets.touch_customer(self.phone, self._now(), name=name, memo=memo)
        if name:
            self.customer_name = name
        return {"ok": True}

    async def _tool_create_quote_request(self, service: str, details: str, location: str = "",
                                         preferred_schedule: str = "") -> dict:
        quote_id = await self.deps.sheets.create_quote(
            self._now(), self.phone, self.customer_name, service, details, location, preferred_schedule,
        )
        await self.deps.notifier.notify(
            f"[견적 요청] {quote_id}\n고객: {self.customer_name or '-'} ({self.phone})\n"
            f"서비스: {service}\n내용: {details}\n장소: {location or '-'}\n희망일정: {preferred_schedule or '-'}\n\n"
            f"시트 '견적' 탭에 금액/내용 입력 후 상태를 '{QuoteStatus.READY}'로 바꾸면 AI가 고객에게 안내합니다."
        )
        return {"quote_id": quote_id, "status": QuoteStatus.WAITING}

    async def _tool_find_available_slots(self, date_from: str, date_to: str,
                                         duration_minutes: int | None = None) -> dict:
        try:
            start_day, end_day = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError as e:
            raise ToolError("날짜 형식은 YYYY-MM-DD 입니다.") from e
        if (end_day - start_day).days > 14:
            end_day = start_day + timedelta(days=14)
        duration = timedelta(minutes=duration_minutes or self.deps.settings.consult_minutes)
        slots = await self.deps.calendar.find_free_slots(start_day, end_day, duration)
        return {"slots": [s.strftime("%Y-%m-%dT%H:%M") for s in slots], "duration_minutes": int(duration.total_seconds() // 60)}

    async def _tool_book_appointment(self, start: str, appointment_type: str, duration_minutes: int | None = None,
                                     quote_id: str = "", notes: str = "") -> dict:
        start_dt = self._parse_local(start)
        end_dt = start_dt + timedelta(minutes=duration_minutes or self.deps.settings.consult_minutes)
        await self._check_slot(start_dt, end_dt)

        label = self.customer_name or self.phone[-4:]
        event_id = await self.deps.calendar.create_event(
            summary=f"[{appointment_type}] {label}",
            start=start_dt, end=end_dt,
            description=f"전화: {self.phone}\n견적ID: {quote_id or '-'}\n메모: {notes}\n(AI 직원 등록)",
            private={"phone": self.phone, "quote_id": quote_id, "type": appointment_type},
        )
        await self.deps.sheets.add_appointment(
            event_id, quote_id, self.phone, self.customer_name, appointment_type, start_dt, end_dt, notes,
        )
        if quote_id:
            await self.deps.sheets.set_quote_status(quote_id, QuoteStatus.SCHEDULED)
        await self.deps.notifier.notify(
            f"[일정 등록] {appointment_type} {start_dt:%m/%d %H:%M}\n고객: {self.customer_name or '-'} ({self.phone})\n{notes}"
        )
        return {"appointment_id": event_id, "start": start_dt.strftime("%Y-%m-%dT%H:%M"), "end": end_dt.strftime("%H:%M")}

    async def _tool_reschedule_appointment(self, appointment_id: str, new_start: str) -> dict:
        appt = await self._own_appointment(appointment_id)
        old_start = self._parse_local(appt["시작"].replace(" ", "T"))
        old_end = self._parse_local(appt["종료"].replace(" ", "T"))
        start_dt = self._parse_local(new_start)
        end_dt = start_dt + (old_end - old_start)
        await self._check_slot(start_dt, end_dt)

        await self.deps.calendar.move_event(appointment_id, start_dt, end_dt)
        await self.deps.sheets.update_appointment(
            appointment_id, 시작=start_dt.strftime("%Y-%m-%d %H:%M"), 종료=end_dt.strftime("%Y-%m-%d %H:%M"),
        )
        await self.deps.notifier.notify(
            f"[일정 변경] {appt['유형']} {old_start:%m/%d %H:%M} -> {start_dt:%m/%d %H:%M}\n고객: {appt['이름'] or '-'} ({self.phone})"
        )
        return {"appointment_id": appointment_id, "start": start_dt.strftime("%Y-%m-%dT%H:%M")}

    async def _tool_cancel_appointment(self, appointment_id: str, reason: str = "") -> dict:
        appt = await self._own_appointment(appointment_id)
        await self.deps.calendar.cancel_event(appointment_id)
        await self.deps.sheets.update_appointment(
            appointment_id, 상태=AppointmentStatus.CANCELLED, 메모=f"{appt['메모']} / 취소사유: {reason}".strip(" /"),
        )
        await self.deps.notifier.notify(
            f"[일정 취소] {appt['유형']} {appt['시작']}\n고객: {appt['이름'] or '-'} ({self.phone})\n사유: {reason or '-'}"
        )
        return {"cancelled": appointment_id}

    async def _tool_escalate_to_owner(self, reason: str, summary: str, urgency: str = "normal") -> dict:
        self.turn.escalated = True
        prefix = "[긴급 확인 필요]" if urgency == "urgent" else "[확인 필요]"
        await self.deps.notifier.notify(
            f"{prefix} {reason}\n고객: {self.customer_name or '-'} ({self.phone})\n{summary}"
        )
        return {"ok": True, "instruction": "고객에게 담당자가 확인 후 연락드린다고 안내할 것. 구체적 약속은 하지 말 것."}
