from app.config import Settings
from app.db import Database
from app.runtime_policy import apply_policy, effective_dry_run, effective_test_numbers, policy_snapshot


def test_runtime_policy_overrides():
    db = Database(":memory:")
    settings = Settings(_env_file=None, anthropic_api_key="x", webhook_token="t", dry_run=False, test_numbers="")
    apply_policy(db, dry_run=True, test_numbers="01011112222", max_replies_per_hour=3)
    assert effective_dry_run(settings, db) is True
    assert effective_test_numbers(settings, db) == {"01011112222"}
    snap = policy_snapshot(settings, db)
    assert snap["max_replies_per_hour"] == 3
    apply_policy(db, reset_test_numbers=True)
    assert effective_test_numbers(settings, db) == set()
