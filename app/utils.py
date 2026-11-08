import re

_MOBILE_RE = re.compile(r"01[016789]\d{7,8}")
_AMOUNT_RE = re.compile(r"(\d[\d,]*)\s*(만\s*)?원")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    # MacroDroid 등에서 선행 0 없이 10자리(1012345678)로 오는 경우
    if len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits


def to_e164(phone: str) -> str:
    digits = normalize_phone(phone)
    return "+82" + digits[1:] if digits.startswith("0") else "+" + digits


def is_korean_mobile(phone: str) -> bool:
    return _MOBILE_RE.fullmatch(normalize_phone(phone)) is not None


def format_phone_display(phone: str) -> str:
    p = normalize_phone(phone)
    if len(p) == 11:
        return f"{p[:3]}-{p[3:7]}-{p[7:]}"
    if len(p) == 10:
        return f"{p[:3]}-{p[3:6]}-{p[6:]}"
    return p


def extract_amounts(text: str) -> set[int]:
    """'15만원', '150,000원' -> {150000}"""
    amounts = set()
    for number, man in _AMOUNT_RE.findall(text or ""):
        value = int(number.replace(",", ""))
        amounts.add(value * 10_000 if man else value)
    return amounts
