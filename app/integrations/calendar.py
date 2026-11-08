"""일정 저장소.

GoogleCalendarClient: 서비스 계정 이메일을 대상 캘린더에 '일정 변경' 권한으로 공유해야 한다.
MemoryCalendar: MOCK_MODE용.
"""

import asyncio
import threading
import time as time_module
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SLOT_STEP = timedelta(minutes=30)
MIN_LEAD_TIME = timedelta(hours=2)
LIST_CACHE_SECONDS = 30


class CalendarBase:
    def __init__(self, tz: ZoneInfo, hours_start: int, hours_end: int, business_days: set[int]):
        self._tz = tz
        self._hours_start = hours_start
        self._hours_end = hours_end
        self._business_days = business_days

    async def busy_periods(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        raise NotImplementedError

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str,
        private: dict[str, str],
        *,
        popup_minutes_before: int | None = None,
    ) -> str:
        raise NotImplementedError

    async def move_event(self, event_id: str, start: datetime, end: datetime) -> None:
        raise NotImplementedError

    async def cancel_event(self, event_id: str) -> None:
        raise NotImplementedError

    async def events_between(self, start: datetime, end: datetime) -> list[dict]:
        raise NotImplementedError

    async def is_free(self, start: datetime, end: datetime) -> bool:
        return not await self.busy_periods(start, end)

    def within_business_hours(self, start: datetime, end: datetime) -> bool:
        local_start, local_end = start.astimezone(self._tz), end.astimezone(self._tz)
        return (
            local_start.weekday() in self._business_days
            and local_start.date() == local_end.date()
            and local_start.time() >= time(self._hours_start)
            and local_end.time() <= time(self._hours_end)
        )

    async def find_free_slots(self, date_from: date, date_to: date, duration: timedelta,
                              max_slots: int = 8) -> list[datetime]:
        range_start = datetime.combine(date_from, time(0), self._tz)
        range_end = datetime.combine(date_to + timedelta(days=1), time(0), self._tz)
        busy = await self.busy_periods(range_start, range_end)
        earliest = datetime.now(self._tz) + MIN_LEAD_TIME

        slots: list[datetime] = []
        day = date_from
        while day <= date_to and len(slots) < max_slots:
            if day.weekday() in self._business_days:
                cursor = datetime.combine(day, time(self._hours_start), self._tz)
                day_end = datetime.combine(day, time(self._hours_end), self._tz)
                while cursor + duration <= day_end and len(slots) < max_slots:
                    end = cursor + duration
                    if cursor >= earliest and all(end <= b_start or cursor >= b_end for b_start, b_end in busy):
                        slots.append(cursor)
                    cursor += SLOT_STEP
            day += timedelta(days=1)
        return slots

    def _event_body(self, start: datetime, end: datetime) -> dict:
        return {
            "start": {"dateTime": start.isoformat(), "timeZone": str(self._tz)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self._tz)},
        }


class GoogleCalendarClient(CalendarBase):
    def __init__(self, service_account_file: str, calendar_id: str, tz: ZoneInfo,
                 hours_start: int, hours_end: int, business_days: set[int]):
        super().__init__(tz, hours_start, hours_end, business_days)
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        self._api = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self._calendar_id = calendar_id
        self._http_lock = threading.Lock()
        self._list_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

    def _execute_locked(self, request):
        # googleapiclient의 httplib2 연결은 스레드 간 동시 사용 시 SSL 오류가 난다.
        with self._http_lock:
            return request.execute(num_retries=2)

    async def _call(self, request):
        return await asyncio.to_thread(self._execute_locked, request)

    async def busy_periods(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        body = {
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "timeZone": str(self._tz), "items": [{"id": self._calendar_id}],
        }
        result = await self._call(self._api.freebusy().query(body=body))
        busy = result["calendars"][self._calendar_id]["busy"]
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy]

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str,
        private: dict[str, str],
        *,
        popup_minutes_before: int | None = None,
    ) -> str:
        body = {
            "summary": summary, "description": description,
            "extendedProperties": {"private": private}, **self._event_body(start, end),
        }
        if popup_minutes_before is not None:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": max(0, popup_minutes_before)}],
            }
        event = await self._call(self._api.events().insert(calendarId=self._calendar_id, body=body))
        self._list_cache.clear()
        return event["id"]

    async def move_event(self, event_id: str, start: datetime, end: datetime) -> None:
        await self._call(self._api.events().patch(
            calendarId=self._calendar_id, eventId=event_id, body=self._event_body(start, end),
        ))
        self._list_cache.clear()

    async def cancel_event(self, event_id: str) -> None:
        await self._call(self._api.events().delete(calendarId=self._calendar_id, eventId=event_id))
        self._list_cache.clear()

    async def events_between(self, start: datetime, end: datetime) -> list[dict]:
        """화면 새로고침용 목록 조회는 잠깐 캐시한다. 예약 가능 여부(busy_periods)는 캐시하지 않는다."""
        key = (start.isoformat(), end.isoformat())
        if (hit := self._list_cache.get(key)) and time_module.monotonic() - hit[0] < LIST_CACHE_SECONDS:
            return hit[1]
        events = await self._events_between_uncached(start, end)
        self._list_cache = {key: (time_module.monotonic(), events)}
        return events

    async def _events_between_uncached(self, start: datetime, end: datetime) -> list[dict]:
        result = await self._call(self._api.events().list(
            calendarId=self._calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime",
        ))
        return result.get("items", [])


class MemoryCalendar(CalendarBase):
    """MOCK_MODE용. 내일/모레에 기존 일정을 몇 개 깔아 두어 빈 시간 찾기가 현실적으로 보이게 한다."""

    def __init__(self, tz: ZoneInfo, hours_start: int, hours_end: int, business_days: set[int]):
        super().__init__(tz, hours_start, hours_end, business_days)
        self._events: dict[str, dict] = {}
        today = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
        for days, hour, length in [(1, 10, 2), (1, 14, 1), (2, 9, 3)]:
            start = (today + timedelta(days=days)).replace(hour=hour)
            self._add("기존 일정 (예시)", start, start + timedelta(hours=length), "", {})

    def _add(self, summary: str, start: datetime, end: datetime, description: str, private: dict) -> str:
        event_id = uuid.uuid4().hex[:12]
        self._events[event_id] = {
            "id": event_id, "summary": summary, "description": description,
            "extendedProperties": {"private": private}, **self._event_body(start, end),
        }
        return event_id

    @staticmethod
    def _range(event: dict) -> tuple[datetime, datetime]:
        return datetime.fromisoformat(event["start"]["dateTime"]), datetime.fromisoformat(event["end"]["dateTime"])

    async def busy_periods(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        periods = [self._range(e) for e in self._events.values()]
        return sorted((s, e) for s, e in periods if s < end and e > start)

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str,
        private: dict[str, str],
        *,
        popup_minutes_before: int | None = None,
    ) -> str:
        return self._add(summary, start, end, description, private)

    async def move_event(self, event_id: str, start: datetime, end: datetime) -> None:
        self._events[event_id].update(self._event_body(start, end))

    async def cancel_event(self, event_id: str) -> None:
        self._events.pop(event_id, None)

    async def events_between(self, start: datetime, end: datetime) -> list[dict]:
        events = [e for e in self._events.values() if self._range(e)[0] < end and self._range(e)[1] > start]
        return sorted(events, key=lambda e: e["start"]["dateTime"])
