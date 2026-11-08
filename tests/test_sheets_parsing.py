from app.integrations.sheets import _to_records


def test_to_records_keeps_row_numbers_and_skips_blank_rows():
    values = [
        ["전화번호", "이름", "첫연락일", "최근연락일", "메모"],
        ["01012345678", "김철수", "2026-10-25 10:00", "2026-10-25 11:00"],
        ["", "", ""],
        ["01099998888", "", "", "", "VIP"],
    ]
    rows = _to_records("고객", values)
    assert [r["_row"] for r in rows] == [2, 4]
    assert rows[0]["메모"] == ""
    assert rows[1]["메모"] == "VIP"


def test_to_records_tolerates_reordered_columns():
    values = [["사유", "전화번호"], ["가족", "01000000000"]]
    [row] = _to_records("제외번호", values)
    assert row["전화번호"] == "01000000000" and row["사유"] == "가족"


def test_to_records_empty_tab():
    assert _to_records("견적", []) == []
