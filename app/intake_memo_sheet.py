"""접수메모 시트 탭 — Keep 규격과 동일한 제목·본문 포맷."""

from __future__ import annotations

import re
from datetime import datetime

MEMO_STATUS = "미연락 (대표 확인 필요)"

INTAKE_MEMO_STATUS_OPTIONS = (
    MEMO_STATUS,
    "연락완료",
    "처리완료",
    "보류",
)

CONSULT_CLASS = {
    "1": "1번 중고차성능보증",
    "2": "2번 일반정비",
    "3": "3번 지인/개인용무",
}

CATEGORY_SHORT = {
    "1": "중고차 성능보증",
    "2": "일반정비",
}

_PLATE_RE = re.compile(r"\d{2,3}[가-힣]\d{4}")
_MENU_ONLY = re.compile(r"^[123](번)?$", re.I)


def _detail_text(lines: list[str]) -> str:
    kept = [ln.strip() for ln in lines if ln.strip() and not _MENU_ONLY.match(ln.strip())]
    if kept and _MENU_ONLY.match(kept[0]):
        kept = kept[1:]
    return "\n".join(kept).strip()


def _extract_plate(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    m = _PLATE_RE.search(compact)
    if m:
        return m.group(0)
    for line in text.splitlines():
        m = _PLATE_RE.search(re.sub(r"\s+", "", line))
        if m:
            return m.group(0)
    return ""


def _field_after_label(text: str, labels: tuple[str, ...]) -> str:
    for line in text.splitlines():
        for lab in labels:
            if lab not in line:
                continue
            if ":" in line or "：" in line:
                parts = re.split(r"[:：]", line, maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    return parts[1].strip()
            rest = line.replace(lab, "", 1).strip(" :-")
            if rest:
                return rest
    return ""


def _guess_car_model(text: str, plate: str) -> str:
    model = _field_after_label(text, ("차종", "모델", "차량명"))
    if model and re.sub(r"\s+", "", model) != plate and not _PLATE_RE.fullmatch(re.sub(r"\s+", "", model)):
        return model
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        compact = re.sub(r"\s+", "", s)
        if plate and plate in compact:
            rest = compact.replace(plate, "", 1).strip()
            if rest and len(rest) >= 2 and not _PLATE_RE.search(rest):
                return rest
            spaced = re.sub(re.escape(plate), "", s, count=1).strip(" ,·-/")
            if spaced and len(spaced) >= 2 and not _PLATE_RE.search(re.sub(r"\s+", "", spaced)):
                return spaced
            continue
        if _PLATE_RE.search(compact):
            continue
        if len(s) >= 2 and not s.startswith("-"):
            return s
    return ""


def _symptom_or_request(category: str, text: str, plate: str, model: str) -> str:
    labeled = _field_after_label(
        text,
        ("문의", "증상", "요청", "보증보험사", "정비 요청", "세부", "내용"),
    )
    if labeled:
        return labeled
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if plate and plate in re.sub(r"\s+", "", s):
            continue
        if model and model in s and len(s) < len(model) + 4:
            continue
        lines.append(s)
    return "\n".join(lines).strip() or text.strip()


def _format_memo_card(title: str, lines: list[str]) -> str:
    """시트·화면 공통 — Keep 스타일 텍스트 카드."""
    bar = "─" * 28
    body = "\n".join(f"· {line}" for line in lines)
    return f"{bar}\n{title}\n{bar}\n\n{body}"


def build_intake_memo_row(
    *,
    category: str,
    phone: str,
    phone_display: str,
    customer_name: str,
    lines: list[str],
    received_at: datetime,
) -> dict[str, str]:
    """Google Keep 규격 → 접수메모 탭 한 행."""
    stamp = received_at.strftime("%Y-%m-%d %H:%M")
    detail = _detail_text(lines)

    if category in ("1", "2"):
        plate = _extract_plate(detail)
        model = _guess_car_model(detail, plate)
        symptoms = _symptom_or_request(category, detail, plate, model)
        detail_field = symptoms or detail or "(미기재)"
        cat_name = CATEGORY_SHORT.get(category, category)
        title = f"[고객접수] {cat_name} - {plate or '차량번호 미기재'}"
        card_lines = [
            f"접수일시: {stamp}",
            f"발신번호: {phone_display}",
            f"상담분류: {CONSULT_CLASS[category]}",
            f"차량번호: {plate or '(미기재)'}",
            f"차종: {model or '(미기재)'}",
            f"문의/증상: {detail_field}",
            f"조치상태: {MEMO_STATUS}",
        ]
        return {
            "조치상태": MEMO_STATUS,
            "등록일시": stamp,
            "상담분류": CONSULT_CLASS[category],
            "제목": title,
            "전화번호": phone,
            "차량번호": plate or "—",
            "차종": model or "—",
            "문의·증상": detail_field,
            "남긴용건": "—",
            "메모전문": _format_memo_card(title, card_lines),
        }

    display = (customer_name or "").strip() or phone_display
    title = f"[개인연락] {display}"
    message = detail or "(내용 없음)"
    card_lines = [
        f"접수일시: {stamp}",
        f"발신번호: {phone_display}",
        f"상담분류: {CONSULT_CLASS['3']}",
        f"남긴용건: {message}",
        f"조치상태: {MEMO_STATUS}",
    ]
    return {
        "조치상태": MEMO_STATUS,
        "등록일시": stamp,
        "상담분류": CONSULT_CLASS["3"],
        "제목": title,
        "전화번호": phone,
        "차량번호": "—",
        "차종": "—",
        "문의·증상": "—",
        "남긴용건": message,
        "메모전문": _format_memo_card(title, card_lines),
    }