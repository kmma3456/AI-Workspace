"""운영 화면에서 바꾸는 응대 정책. SQLite flags 우선, 없으면 .env 기본값."""

from app.config import Settings
from app.db import Database
from app.utils import normalize_phone

FLAG_DRY_RUN = "policy_dry_run"
FLAG_TEST_NUMBERS = "policy_test_numbers"
FLAG_MAX_REPLIES = "policy_max_replies_per_hour"
FLAG_BATCH_WINDOW = "policy_batch_window_seconds"


def effective_dry_run(settings: Settings, db: Database) -> bool:
    v = db.get_flag(FLAG_DRY_RUN)
    if v == "1":
        return True
    if v == "0":
        return False
    return settings.dry_run


def effective_test_numbers(settings: Settings, db: Database) -> set[str]:
    v = db.get_flag(FLAG_TEST_NUMBERS).strip()
    if v:
        return {normalize_phone(n) for n in v.split(",") if n.strip()}
    return settings.test_number_set


def effective_max_replies(settings: Settings, db: Database) -> int:
    v = db.get_flag(FLAG_MAX_REPLIES).strip()
    if v.isdigit():
        return max(1, int(v))
    return settings.max_replies_per_hour


def effective_batch_window(settings: Settings, db: Database) -> int:
    v = db.get_flag(FLAG_BATCH_WINDOW).strip()
    if v.isdigit():
        return max(5, int(v))
    return settings.batch_window_seconds


def policy_snapshot(settings: Settings, db: Database) -> dict:
    return {
        "dry_run": effective_dry_run(settings, db),
        "dry_run_overridden": bool(db.get_flag(FLAG_DRY_RUN)),
        "test_numbers": sorted(effective_test_numbers(settings, db)),
        "test_numbers_overridden": bool(db.get_flag(FLAG_TEST_NUMBERS).strip()),
        "max_replies_per_hour": effective_max_replies(settings, db),
        "max_replies_overridden": bool(db.get_flag(FLAG_MAX_REPLIES).strip()),
        "batch_window_seconds": effective_batch_window(settings, db),
        "batch_window_overridden": bool(db.get_flag(FLAG_BATCH_WINDOW).strip()),
        "env_defaults": {
            "dry_run": settings.dry_run,
            "test_numbers": sorted(settings.test_number_set),
            "max_replies_per_hour": settings.max_replies_per_hour,
            "batch_window_seconds": settings.batch_window_seconds,
        },
    }


def apply_policy(
    db: Database,
    *,
    dry_run: bool | None = None,
    test_numbers: str | None = None,
    max_replies_per_hour: int | None = None,
    batch_window_seconds: int | None = None,
    reset_test_numbers: bool = False,
) -> None:
    if dry_run is not None:
        db.set_flag(FLAG_DRY_RUN, "1" if dry_run else "0")
    if reset_test_numbers:
        db.set_flag(FLAG_TEST_NUMBERS, "")
    elif test_numbers is not None:
        db.set_flag(FLAG_TEST_NUMBERS, test_numbers.strip())
    if max_replies_per_hour is not None:
        db.set_flag(FLAG_MAX_REPLIES, str(max(1, max_replies_per_hour)))
    if batch_window_seconds is not None:
        db.set_flag(FLAG_BATCH_WINDOW, str(max(5, batch_window_seconds)))
