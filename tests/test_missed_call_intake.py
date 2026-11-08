from app.missed_call_intake import parse_menu_choice


def test_parse_menu_digit():
    assert parse_menu_choice("1") == "1"
    assert parse_menu_choice(" 2 ") == "2"


def test_parse_menu_korean():
    assert parse_menu_choice("3번") == "3"


def test_parse_menu_invalid():
    assert parse_menu_choice("안녕") is None
    assert parse_menu_choice("") is None
