"""구글 시트 저장소. 사장님이 직접 보고 고치는 곳이라 탭/열 이름은 한글로 둔다.

SheetsBase: 업무 로직 / GoogleSheetsRepo: 실제 구글 시트 / MemorySheetsRepo: MOCK_MODE용 메모리 저장소
"""

import asyncio
import copy
import time
import uuid
from datetime import datetime

from app.utils import normalize_phone

TAB_HEADERS: dict[str, list[str]] = {
    "고객": ["전화번호", "이름", "첫연락일", "최근연락일", "메모"],
    "문의로그": ["일시", "전화번호", "채널", "분류", "요약", "고객메시지", "AI답변", "처리상태"],
    "견적": [
        "견적ID", "요청일", "전화번호", "이름", "서비스", "상세내용", "장소", "희망일정",
        "상태", "견적금액", "견적내용", "발송일",
    ],
    "일정": ["일정ID", "견적ID", "전화번호", "이름", "유형", "시작", "종료", "상태", "메모"],
    "지식베이스": ["분류", "질문", "답변"],
    "제외번호": ["전화번호", "사유"],
    "설정": ["키", "값"],
    "접수메모": [
        "조치상태",
        "등록일시",
        "상담분류",
        "제목",
        "전화번호",
        "차량번호",
        "차종",
        "문의·증상",
        "남긴용건",
        "메모전문",
    ],
}


class QuoteStatus:
    WAITING = "견적대기"      # AI가 요청 접수, 사장님 견적 작성 필요
    READY = "견적완료"        # 사장님이 금액/내용 입력 후 이 값으로 변경 -> AI가 고객에게 발송
    SENT = "견적발송"
    SCHEDULED = "일정확정"
    CLOSED = "종료"


class AppointmentStatus:
    CONFIRMED = "확정"
    CANCELLED = "취소"


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


class SheetsBase:
    async def records(self, tab: str, *, cached: bool = False) -> list[dict]:
        raise NotImplementedError

    async def append(self, tab: str, row: dict) -> None:
        raise NotImplementedError

    async def update_where(self, tab: str, key_col: str, key_val: str, updates: dict) -> bool:
        raise NotImplementedError

    async def update_row_at(self, tab: str, sheet_row: int, updates: dict[str, str]) -> bool:
        raise NotImplementedError

    # ---------- 설정 / 지식 ----------

    async def ai_enabled(self) -> bool:
        for row in await self.records("설정", cached=True):
            if row["키"] == "AI_ENABLED":
                return row["값"].strip().upper() not in ("OFF", "FALSE", "0", "N")
        return True

    async def get_setting(self, key: str, default: str = "") -> str:
        for row in await self.records("설정", cached=True):
            if row["키"] == key:
                return str(row.get("값", "")).strip()
        return default

    async def set_setting(self, key: str, value: str) -> None:
        for row in await self.records("설정", cached=True):
            if row["키"] == key:
                await self.update_where("설정", "키", key, {"값": value})
                return
        await self.append("설정", {"키": key, "값": value})

    async def knowledge(self) -> list[dict]:
        return await self.records("지식베이스", cached=True)

    async def excluded_numbers(self) -> set[str]:
        rows = await self.records("제외번호", cached=True)
        return {normalize_phone(r["전화번호"]) for r in rows if r["전화번호"]}

    # ---------- 고객 ----------

    async def get_customer(self, phone: str) -> dict | None:
        for row in await self.records("고객"):
            if normalize_phone(row["전화번호"]) == phone:
                return row
        return None

    async def touch_customer(self, phone: str, now: datetime, name: str = "", memo: str = "") -> None:
        existing = await self.get_customer(phone)
        if existing is None:
            await self.append("고객", {"전화번호": phone, "이름": name, "첫연락일": _stamp(now),
                                     "최근연락일": _stamp(now), "메모": memo})
            return
        updates = {"최근연락일": _stamp(now)}
        if name:
            updates["이름"] = name
        if memo:
            updates["메모"] = f"{existing['메모']}\n{memo}".strip()
        await self.update_where("고객", "전화번호", existing["전화번호"], updates)

    # ---------- 문의 로그 ----------

    async def log_inquiry(
        self, now: datetime, phone: str, channel: str, category: str, summary: str,
        customer_message: str, ai_reply: str, status: str,
    ) -> None:
        await self.append("문의로그", {
            "일시": _stamp(now), "전화번호": phone, "채널": channel, "분류": category,
            "요약": summary, "고객메시지": customer_message, "AI답변": ai_reply, "처리상태": status,
        })

    # ---------- 견적 ----------

    async def create_quote(self, now: datetime, phone: str, name: str, service: str, details: str,
                           location: str, preferred_schedule: str) -> str:
        quote_id = f"Q{now:%y%m%d}-{uuid.uuid4().hex[:4].upper()}"
        await self.append("견적", {
            "견적ID": quote_id, "요청일": _stamp(now), "전화번호": phone, "이름": name,
            "서비스": service, "상세내용": details, "장소": location, "희망일정": preferred_schedule,
            "상태": QuoteStatus.WAITING,
        })
        return quote_id

    async def open_quotes(self) -> list[dict]:
        return [r for r in await self.records("견적") if r["상태"] != QuoteStatus.CLOSED]

    async def quotes_for(self, phone: str) -> list[dict]:
        return [r for r in await self.open_quotes() if normalize_phone(r["전화번호"]) == phone]

    async def quotes_by_status(self, status: str) -> list[dict]:
        return [r for r in await self.records("견적") if r["상태"] == status]

    async def set_quote_status(self, quote_id: str, status: str, **extra: str) -> bool:
        return await self.update_where("견적", "견적ID", quote_id, {"상태": status, **extra})

    # ---------- 일정 ----------

    async def add_appointment(self, event_id: str, quote_id: str, phone: str, name: str, kind: str,
                              start: datetime, end: datetime, memo: str) -> None:
        await self.append("일정", {
            "일정ID": event_id, "견적ID": quote_id, "전화번호": phone, "이름": name, "유형": kind,
            "시작": _stamp(start), "종료": _stamp(end), "상태": AppointmentStatus.CONFIRMED, "메모": memo,
        })

    async def appointments_for(self, phone: str) -> list[dict]:
        return [
            r for r in await self.records("일정")
            if normalize_phone(r["전화번호"]) == phone and r["상태"] == AppointmentStatus.CONFIRMED
        ]

    async def upcoming_appointments(self, now: datetime) -> list[dict]:
        rows = [
            r for r in await self.records("일정")
            if r["상태"] == AppointmentStatus.CONFIRMED and r["시작"] >= _stamp(now)
        ]
        return sorted(rows, key=lambda r: r["시작"])

    async def update_appointment(self, event_id: str, **updates: str) -> bool:
        return await self.update_where("일정", "일정ID", event_id, updates)


def _to_records(tab: str, values: list[list[str]]) -> list[dict]:
    """시트 값 -> dict 목록. '_row'는 실제 시트 행 번호 (수정할 때 사용)."""
    if not values:
        return []
    sheet_headers = values[0]
    positions = {h: sheet_headers.index(h) for h in TAB_HEADERS[tab] if h in sheet_headers}
    records = []
    for offset, raw in enumerate(values[1:]):
        if not any(cell.strip() for cell in raw):
            continue
        record = {h: (raw[positions[h]] if h in positions and positions[h] < len(raw) else "") for h in TAB_HEADERS[tab]}
        record["_row"] = offset + 2
        records.append(record)
    return records


class GoogleSheetsRepo(SheetsBase):
    """구글 시트 읽기 한도(분당 60회) 때문에 모든 탭을 요청 한 번으로 읽어 잠깐 캐시한다.

    사장님이 시트를 직접 고친 내용은 최대 data_ttl(설정류 탭은 settings_ttl)초 뒤에 반영된다.
    """

    def __init__(self, service_account_file: str, sheet_id: str, data_ttl: float = 15, settings_ttl: float = 120):
        import gspread
        from gspread.http_client import BackOffHTTPClient

        self._gc = gspread.service_account(filename=service_account_file, http_client=BackOffHTTPClient)
        self._sheet = self._gc.open_by_key(sheet_id)
        self._data_ttl = data_ttl
        self._settings_ttl = settings_ttl
        self._worksheets: dict = {}
        self._snapshot: dict[str, list[dict]] = {}
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def _ws(self, tab: str):
        if tab not in self._worksheets:
            self._worksheets = {ws.title: ws for ws in self._sheet.worksheets()}
        return self._worksheets[tab]

    def _load_all_sync(self) -> dict[str, list[dict]]:
        tabs = list(TAB_HEADERS)
        result = self._sheet.values_batch_get([f"'{t}'" for t in tabs])
        return {t: _to_records(t, vr.get("values", [])) for t, vr in zip(tabs, result["valueRanges"])}

    def _invalidate(self) -> None:
        self._loaded_at = 0.0

    async def records(self, tab: str, *, cached: bool = False) -> list[dict]:
        ttl = self._settings_ttl if cached else self._data_ttl
        async with self._lock:
            if tab not in self._snapshot or time.monotonic() - self._loaded_at > ttl:
                self._snapshot = await asyncio.to_thread(self._load_all_sync)
                self._loaded_at = time.monotonic()
        return copy.deepcopy(self._snapshot[tab])

    async def append(self, tab: str, row: dict) -> None:
        values = [str(row.get(h, "")) for h in TAB_HEADERS[tab]]
        await asyncio.to_thread(self._ws(tab).append_row, values, value_input_option="RAW")
        self._invalidate()

    async def update_where(self, tab: str, key_col: str, key_val: str, updates: dict) -> bool:
        from gspread.utils import rowcol_to_a1

        ws = self._ws(tab)
        rows = _to_records(tab, await asyncio.to_thread(ws.get_all_values))
        headers = TAB_HEADERS[tab]
        for row in rows:
            if row.get(key_col) == key_val:
                cells = [
                    {"range": rowcol_to_a1(row["_row"], headers.index(col) + 1), "values": [[str(val)]]}
                    for col, val in updates.items()
                ]
                await asyncio.to_thread(ws.batch_update, cells, value_input_option="RAW")
                self._invalidate()
                return True
        return False

    async def update_row_at(self, tab: str, sheet_row: int, updates: dict[str, str]) -> bool:
        from gspread.utils import rowcol_to_a1

        headers = TAB_HEADERS[tab]
        ws = self._ws(tab)
        cells = [
            {"range": rowcol_to_a1(sheet_row, headers.index(col) + 1), "values": [[str(val)]]}
            for col, val in updates.items()
            if col in headers
        ]
        if not cells:
            return False
        await asyncio.to_thread(ws.batch_update, cells, value_input_option="RAW")
        self._invalidate()
        return True

    def ensure_tabs(self) -> list[str]:
        self._worksheets = {}
        existing = {ws.title for ws in self._sheet.worksheets()}
        created = []
        for tab, headers in TAB_HEADERS.items():
            if tab not in existing:
                ws = self._sheet.add_worksheet(title=tab, rows=1000, cols=len(headers))
                ws.append_row(headers, value_input_option="RAW")
                created.append(tab)
            elif tab == "접수메모":
                ws = self._ws(tab)
                row1 = ws.row_values(1)
                if row1 != headers:
                    ws.update([headers], "A1", value_input_option="RAW")
        return created


DEMO_KNOWLEDGE = [
    {"분류": "영업시간", "질문": "영업시간이 어떻게 되나요?", "답변": "월~토 오전 9시~오후 6시, 일요일은 휴무입니다."},
    {"분류": "견적", "질문": "견적은 어떻게 받나요?",
     "답변": "문자로 원하시는 작업 내용, 장소, 희망 일정을 알려주시면 담당자가 확인 후 견적을 보내드립니다. 현장 확인이 필요하면 방문견적 일정을 잡아드립니다."},
    {"분류": "비용", "질문": "방문견적 비용이 있나요?", "답변": "방문견적 출장비는 2만원이며, 계약하시면 작업비에서 차감됩니다."},
    {"분류": "지역", "질문": "어느 지역까지 가능한가요?", "답변": "서울 전 지역과 경기 성남, 하남, 구리까지 가능합니다."},
    {"분류": "결제", "질문": "결제는 어떻게 하나요?", "답변": "작업 완료 후 계좌이체 또는 카드결제로 하시면 됩니다."},
]


class MemorySheetsRepo(SheetsBase):
    """MOCK_MODE용. 서버를 끄면 사라진다."""

    def __init__(self):
        self._tabs: dict[str, list[dict]] = {tab: [] for tab in TAB_HEADERS}
        self._tabs["지식베이스"] = copy.deepcopy(DEMO_KNOWLEDGE)
        self._tabs["제외번호"] = [{"전화번호": "01000000000", "사유": "가족 (예시)"}]
        self._tabs["설정"] = [{"키": "AI_ENABLED", "값": "ON"}]

    async def records(self, tab: str, *, cached: bool = False) -> list[dict]:
        return copy.deepcopy(self._tabs[tab])

    async def append(self, tab: str, row: dict) -> None:
        entry = {h: str(row.get(h, "")) for h in TAB_HEADERS[tab]}
        entry["_row"] = len(self._tabs[tab]) + 2
        self._tabs[tab].append(entry)

    async def update_where(self, tab: str, key_col: str, key_val: str, updates: dict) -> bool:
        for row in self._tabs[tab]:
            if row.get(key_col) == key_val:
                row.update({k: str(v) for k, v in updates.items()})
                return True
        return False

    async def update_row_at(self, tab: str, sheet_row: int, updates: dict[str, str]) -> bool:
        for row in self._tabs[tab]:
            if row.get("_row") == sheet_row:
                row.update({k: str(v) for k, v in updates.items()})
                return True
        idx = sheet_row - 2
        if 0 <= idx < len(self._tabs[tab]):
            self._tabs[tab][idx].update({k: str(v) for k, v in updates.items()})
            return True
        return False
