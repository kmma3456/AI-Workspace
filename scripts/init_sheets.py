"""구글 시트에 필요한 탭과 헤더를 만든다.  python -m scripts.init_sheets"""

from app.config import get_settings
from app.integrations.sheets import GoogleSheetsRepo


def main() -> None:
    settings = get_settings()
    repo = GoogleSheetsRepo(settings.google_service_account_file, settings.google_sheet_id)
    created = repo.ensure_tabs()
    print("생성한 탭:", ", ".join(created) if created else "(없음, 이미 모두 있음)")
    if "설정" in created:
        repo._ws("설정").append_row(["AI_ENABLED", "ON"], value_input_option="RAW")


if __name__ == "__main__":
    main()
