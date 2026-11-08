from app.urgent_keywords import find_urgent_partner_keywords


def test_finds_keyword_in_message():
    assert find_urgent_partner_keywords("신한 쪽에서 연락드립니다") == ["신한"]


def test_finds_db_case_insensitive():
    assert find_urgent_partner_keywords("db손해보험 안내") == ["DB"]


def test_sender_label():
    assert find_urgent_partner_keywords("안녕하세요", "삼성 담당") == ["삼성"]


def test_multiple_keywords():
    matched = find_urgent_partner_keywords("한화와 신한 동시")
    assert matched == ["신한", "한화"]


def test_no_match():
    assert find_urgent_partner_keywords("일반 문의입니다") == []
