"""구글 시트/캘린더 연결 점검.  python -m scripts.check_google [--init]

--init: 점검 통과 시 시트에 필요한 탭과 헤더를 만든다.
"""

import json
import sys
from pathlib import Path

from app.config import get_settings
from app.integrations.sheets import TAB_HEADERS

OK, FAIL, WARN = "[OK]  ", "[실패]", "[주의]"


def explain_api_error(text: str, api_name: str) -> str:
    if "has not been used" in text or "is disabled" in text or "SERVICE_DISABLED" in text:
        return f"{api_name} 가 꺼져 있습니다. Google Cloud 콘솔 > API 및 서비스 > 라이브러리에서 '사용'을 누르세요."
    if "403" in text or "PERMISSION_DENIED" in text or "forbidden" in text.lower():
        return "서비스 계정에 공유가 안 되어 있습니다. 위의 서비스 계정 이메일을 편집 권한으로 공유하세요."
    if "404" in text or "notFound" in text or "not found" in text.lower():
        return "ID가 틀렸거나, 서비스 계정에 공유가 안 되어 있습니다."
    return text[:300]


def check_credentials(path: str) -> str | None:
    file = Path(path)
    if not file.exists():
        print(f"{FAIL} 서비스 계정 키 파일이 없습니다: {file.resolve()}")
        print("       Google Cloud 콘솔에서 받은 JSON 파일을 이 경로에 이 이름으로 두세요.")
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        email = data["client_email"]
    except (json.JSONDecodeError, KeyError):
        print(f"{FAIL} 키 파일 형식이 올바르지 않습니다. 서비스 계정 '키' 탭에서 JSON으로 다시 받으세요.")
        return None
    print(f"{OK} 서비스 계정 키 파일 확인")
    print(f"       서비스 계정 이메일: {email}")
    print("       -> 시트와 캘린더를 이 이메일에 공유해야 합니다.")
    return email


def check_sheet(path: str, sheet_id: str) -> object | None:
    if not sheet_id:
        print(f"{FAIL} .env 의 GOOGLE_SHEET_ID 가 비어 있습니다. (시트 주소 /d/ 와 /edit 사이의 값)")
        return None
    from app.integrations.sheets import GoogleSheetsRepo

    try:
        repo = GoogleSheetsRepo(path, sheet_id)
        tabs = [ws.title for ws in repo._sheet.worksheets()]
    except Exception as e:
        print(f"{FAIL} 구글 시트 접근 실패: {explain_api_error(repr(e), 'Google Sheets API')}")
        return None
    print(f"{OK} 구글 시트 접근: '{repo._sheet.title}'")
    missing = [t for t in TAB_HEADERS if t not in tabs]
    if missing:
        print(f"{WARN} 없는 탭: {', '.join(missing)}  -> --init 옵션으로 자동 생성할 수 있습니다.")
    else:
        print(f"{OK} 필요한 탭 모두 있음")
    return repo


def check_calendar(path: str, calendar_id: str, tz) -> bool:
    if not calendar_id:
        print(f"{FAIL} .env 의 GOOGLE_CALENDAR_ID 가 비어 있습니다. (캘린더 설정 > 캘린더 통합 > 캘린더 ID)")
        return False
    from datetime import datetime, timedelta

    from googleapiclient.errors import HttpError

    from app.integrations.calendar import GoogleCalendarClient

    try:
        cal = GoogleCalendarClient(path, calendar_id, tz, 9, 18, set(range(7)))
        now = datetime.now(tz)
        events = cal._api.events().list(
            calendarId=calendar_id, timeMin=now.isoformat(), timeMax=(now + timedelta(days=14)).isoformat(),
            singleEvents=True, maxResults=5,
        ).execute()
        acl_role = events.get("accessRole", "")
    except HttpError as e:
        print(f"{FAIL} 구글 캘린더 접근 실패: {explain_api_error(f'{e.status_code} {e}', 'Google Calendar API')}")
        return False
    except Exception as e:
        print(f"{FAIL} 구글 캘린더 접근 실패: {explain_api_error(repr(e), 'Google Calendar API')}")
        return False

    print(f"{OK} 구글 캘린더 읽기 (권한: {acl_role}, 14일 내 일정 {len(events.get('items', []))}개)")

    probe_start = now + timedelta(days=365)
    try:
        event = cal._api.events().insert(calendarId=calendar_id, body={
            "summary": "[AI 직원 연결 테스트 - 자동 삭제]",
            **cal._event_body(probe_start, probe_start + timedelta(minutes=15)),
        }).execute()
        cal._api.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
    except HttpError as e:
        print(f"{FAIL} 캘린더에 일정을 쓸 수 없습니다 (권한: {acl_role}). 공유 권한을 '일정 변경'으로 바꾸세요. ({e.status_code})")
        return False
    print(f"{OK} 구글 캘린더 쓰기 (테스트 일정 생성/삭제 성공)")
    return True


def main() -> None:
    settings = get_settings()
    print("=== 구글 연결 점검 ===")
    email = check_credentials(settings.google_service_account_file)
    if not email:
        sys.exit(1)
    repo = check_sheet(settings.google_service_account_file, settings.google_sheet_id)
    calendar_ok = check_calendar(settings.google_service_account_file, settings.google_calendar_id, settings.tz)

    if repo and "--init" in sys.argv:
        created = repo.ensure_tabs()
        if "설정" in created:
            repo._ws("설정").append_row(["AI_ENABLED", "ON"], value_input_option="RAW")
        print(f"{OK} 탭 생성: {', '.join(created) if created else '(이미 모두 있음)'}")

    if repo and calendar_ok:
        print("\n모두 통과했습니다. .env 에서 MOCK_MODE=false 로 바꾸고 서버를 재시작하세요.")
        if settings.mock_mode:
            print(f"{WARN} 지금은 MOCK_MODE=true 라서 서버가 아직 가짜 데이터를 쓰고 있습니다.")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
