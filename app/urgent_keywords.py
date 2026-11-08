"""협회·보험사 긴급 문자 — 일반 AI 시나리오 생략 후 캘린더 직통."""

URGENT_PARTNER_KEYWORDS: tuple[str, ...] = (
    "제이피",
    "삼성",
    "DB",
    "이룸",
    "오토젠스",
    "전성협",
    "카뷰",
    "신한",
    "한화",
)


def find_urgent_partner_keywords(*parts: str) -> list[str]:
    """메시지 본문·발신 표시명 등에 키워드가 포함되면 매칭 (등록 순서 유지, 중복 제거)."""
    combined = "\n".join(p.strip() for p in parts if p and p.strip())
    if not combined:
        return []
    lower = combined.lower()
    seen: set[str] = set()
    matched: list[str] = []
    for kw in URGENT_PARTNER_KEYWORDS:
        if kw in seen:
            continue
        if kw.lower() in lower or kw in combined:
            matched.append(kw)
            seen.add(kw)
    return matched
