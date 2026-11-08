from datetime import datetime, timedelta, timezone

from app.db import Database, Source


def test_search_messages_filter_and_pagination():
    db = Database(":memory:")
    for i in range(5):
        db.add_message("01011112222", Source.CUSTOMER, f"문의 {i}")
    db.add_message("01033334444", Source.AI, "답변입니다")

    all_res = db.search_messages(limit=2, offset=0)
    assert all_res["total"] == 6
    assert len(all_res["items"]) == 2

    page2 = db.search_messages(limit=2, offset=2)
    assert len(page2["items"]) == 2
    assert page2["offset"] == 2

    ai_only = db.search_messages(source=Source.AI)
    assert ai_only["total"] == 1
    assert ai_only["items"][0]["content"] == "답변입니다"

    by_q = db.search_messages(q="0103333")
    assert by_q["total"] == 1

    by_content = db.search_messages(q="문의 3")
    assert by_content["total"] == 1

    db.add_message("01062406816", Source.TEMPLATE, "부재중 안내")
    by_hyphen = db.search_messages(q="010-6240-6816")
    assert by_hyphen["total"] == 1
    assert by_hyphen["items"][0]["phone"] == "01062406816"

    now = datetime.now(timezone.utc)
    ranged = db.search_messages(since=now - timedelta(days=1), until=now + timedelta(minutes=1))
    assert ranged["total"] == 7
    empty = db.search_messages(until=now - timedelta(days=2))
    assert empty["total"] == 0
