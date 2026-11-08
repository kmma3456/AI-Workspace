"""야간·새벽 응대 제한 — 긴급/부재중 캘린더를 익일 연락 시각으로 미루고 고객 안내 문자 발송."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import Database

FLAG_ENABLED = "quiet_hours_enabled"
FLAG_START = "quiet_hours_start"
FLAG_END = "quiet_hours_end"

DEFAULT_START = "00:00"
DEFAULT_END = "09:00"


def parse_time_label(raw: str) -> tuple[int, int]:
    s = (raw or "").strip()
    if s in ("24:00", "24:00:00"):
        return 0, 0
    if not s:
        return 0, 0
    parts = s.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if h == 24:
        h = 0
    return max(0, min(23, h)), max(0, min(59, m))


def format_time_label(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def is_quiet_now(
    now: datetime,
    *,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
) -> bool:
    t = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def defer_contact_datetime(now: datetime, end_hour: int, end_minute: int, tz: ZoneInfo) -> datetime:
    """제한 시간대 접수 → 익일 연락 시각(end + 1초)."""
    local = now.astimezone(tz)
    next_day = local.date() + timedelta(days=1)
    return datetime(
        next_day.year,
        next_day.month,
        next_day.day,
        end_hour,
        end_minute,
        1,
        tzinfo=tz,
    )


def build_defer_customer_sms(business_name: str, contact_at: datetime) -> str:
    when = contact_at.strftime("%m월 %d일 %H:%M")
    return (
        f"안녕하세요, {business_name}입니다.\n"
        f"현재는 응대 시간 외입니다. 익일 영업 시작 후({when}) 순차적으로 연락드리겠습니다.\n"
        "감사합니다."
    )


class QuietHoursConfig:
    __slots__ = ("enabled", "start_hour", "start_minute", "end_hour", "end_minute", "start_label", "end_label")

    def __init__(
        self,
        *,
        enabled: bool,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        start_label: str,
        end_label: str,
    ):
        self.enabled = enabled
        self.start_hour = start_hour
        self.start_minute = start_minute
        self.end_hour = end_hour
        self.end_minute = end_minute
        self.start_label = start_label
        self.end_label = end_label

    def active_at(self, moment: datetime) -> bool:
        if not self.enabled:
            return False
        return is_quiet_now(
            moment,
            start_hour=self.start_hour,
            start_minute=self.start_minute,
            end_hour=self.end_hour,
            end_minute=self.end_minute,
        )


def effective_quiet_hours(settings: Settings, db: Database) -> QuietHoursConfig:
    enabled = db.get_flag(FLAG_ENABLED) == "1"
    start_label = db.get_flag(FLAG_START).strip() or DEFAULT_START
    end_label = db.get_flag(FLAG_END).strip() or DEFAULT_END
    sh, sm = parse_time_label(start_label)
    eh, em = parse_time_label(end_label)
    return QuietHoursConfig(
        enabled=enabled,
        start_hour=sh,
        start_minute=sm,
        end_hour=eh,
        end_minute=em,
        start_label=format_time_label(sh, sm),
        end_label=format_time_label(eh, em),
    )


def quiet_hours_snapshot(settings: Settings, db: Database) -> dict:
    cfg = effective_quiet_hours(settings, db)
    return {
        "enabled": cfg.enabled,
        "start": cfg.start_label,
        "end": cfg.end_label,
        "display_range": f"{cfg.start_label} ~ {cfg.end_label}",
        "defaults": {"enabled": False, "start": DEFAULT_START, "end": DEFAULT_END},
    }


def apply_quiet_hours(
    db: Database,
    *,
    enabled: bool | None = None,
    start: str | None = None,
    end: str | None = None,
) -> None:
    if enabled is not None:
        db.set_flag(FLAG_ENABLED, "1" if enabled else "0")
    if start is not None:
        sh, sm = parse_time_label(start)
        db.set_flag(FLAG_START, format_time_label(sh, sm))
    if end is not None:
        eh, em = parse_time_label(end)
        db.set_flag(FLAG_END, format_time_label(eh, em))


def resolve_calendar_start(
    now: datetime,
    normal_start: datetime,
    cfg: QuietHoursConfig,
    tz: ZoneInfo,
) -> tuple[datetime, bool]:
    if cfg.active_at(now):
        return defer_contact_datetime(now, cfg.end_hour, cfg.end_minute, tz), True
    return normal_start, False
