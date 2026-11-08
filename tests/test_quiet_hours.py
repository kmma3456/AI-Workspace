from datetime import datetime
from zoneinfo import ZoneInfo

from app.quiet_hours import (
    defer_contact_datetime,
    is_quiet_now,
    parse_time_label,
    resolve_calendar_start,
    QuietHoursConfig,
)

TZ = ZoneInfo("Asia/Seoul")


def test_parse_24_as_midnight():
    assert parse_time_label("24:00") == (0, 0)


def test_quiet_overnight_wrap():
    cfg = QuietHoursConfig(
        enabled=True,
        start_hour=22,
        start_minute=0,
        end_hour=9,
        end_minute=0,
        start_label="22:00",
        end_label="09:00",
    )
    assert cfg.active_at(datetime(2026, 3, 10, 23, 30, tzinfo=TZ))
    assert not cfg.active_at(datetime(2026, 3, 10, 12, 0, tzinfo=TZ))
    assert cfg.active_at(datetime(2026, 3, 10, 8, 0, tzinfo=TZ))


def test_quiet_midnight_to_morning():
    assert is_quiet_now(
        datetime(2026, 3, 10, 2, 0, tzinfo=TZ),
        start_hour=0,
        start_minute=0,
        end_hour=9,
        end_minute=0,
    )
    assert not is_quiet_now(
        datetime(2026, 3, 10, 10, 0, tzinfo=TZ),
        start_hour=0,
        start_minute=0,
        end_hour=9,
        end_minute=0,
    )


def test_defer_next_day_at_end_plus_one_second():
    now = datetime(2026, 3, 10, 2, 30, tzinfo=TZ)
    target = defer_contact_datetime(now, 9, 0, TZ)
    assert target == datetime(2026, 3, 11, 9, 0, 1, tzinfo=TZ)


def test_resolve_calendar_defers_during_quiet():
    cfg = QuietHoursConfig(
        enabled=True,
        start_hour=0,
        start_minute=0,
        end_hour=9,
        end_minute=0,
        start_label="00:00",
        end_label="09:00",
    )
    now = datetime(2026, 3, 10, 3, 0, tzinfo=TZ)
    normal = now.replace(hour=4)
    start, deferred = resolve_calendar_start(now, normal, cfg, TZ)
    assert deferred
    assert start == datetime(2026, 3, 11, 9, 0, 1, tzinfo=TZ)
