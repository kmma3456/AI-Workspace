from datetime import datetime
from zoneinfo import ZoneInfo

from app.intake_memo_sheet import build_intake_memo_row


def test_memo_category_2():
    tz = ZoneInfo("Asia/Seoul")
    row = build_intake_memo_row(
        category="2",
        phone="01012345678",
        phone_display="010-1234-5678",
        customer_name="",
        lines=["2", "12가3456 아반떼", "브레이크 소음 점검 부탁"],
        received_at=datetime(2026, 10, 28, 22, 15, tzinfo=tz),
    )
    assert row["제목"] == "[고객접수] 일반정비 - 12가3456"
    assert row["차종"] == "아반떼"
    assert "1번" not in row["상담분류"]
    assert "2번 일반정비" in row["상담분류"]
    assert "브레이크" in row["문의·증상"]
    assert row["남긴용건"] == "—"
    assert "접수일시" in row["메모전문"]
    assert row["조치상태"] == "미연락 (대표 확인 필요)"


def test_memo_category_2_labeled_lines():
    tz = ZoneInfo("Asia/Seoul")
    detail = "- 차량 번호: 240누5045\n- 차종: 레이\n- 정비 요청사항: 오일누수점검"
    row = build_intake_memo_row(
        category="2",
        phone="01062406816",
        phone_display="010-6240-6816",
        customer_name="",
        lines=["2", detail],
        received_at=datetime(2026, 10, 29, 22, 34, tzinfo=tz),
    )
    assert row["차량번호"] == "240누5045"
    assert row["차종"] == "레이"
    assert "오일누수" in row["문의·증상"]


def test_memo_category_3():
    tz = ZoneInfo("Asia/Seoul")
    row = build_intake_memo_row(
        category="3",
        phone="01099998888",
        phone_display="010-9999-8888",
        customer_name="김지인",
        lines=["3", "대표님/meeting 내일 가능한지"],
        received_at=datetime(2026, 10, 28, 9, 0, tzinfo=tz),
    )
    assert row["제목"] == "[개인연락] 김지인"
    assert "남긴용건" in row["메모전문"]
    assert "meeting" in row["남긴용건"]
    assert "3번 지인/개인용무" in row["상담분류"]
