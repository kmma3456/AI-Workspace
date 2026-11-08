from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import Settings
from app.db import Database
from app.integrations.calendar import CalendarBase, GoogleCalendarClient, MemoryCalendar
from app.integrations.notifier import OwnerNotifier
from app.integrations.sheets import GoogleSheetsRepo, MemorySheetsRepo, SheetsBase
from app.integrations.sms_gateway import MacroDroidGateway, MockSmsGateway, SmsGateway


@dataclass
class Deps:
    settings: Settings
    db: Database
    sheets: SheetsBase
    calendar: CalendarBase
    gateway: SmsGateway | MacroDroidGateway | MockSmsGateway
    notifier: OwnerNotifier
    claude: AsyncAnthropic

    async def aclose(self) -> None:
        await self.gateway.aclose()
        await self.notifier.aclose()
        await self.claude.close()
        self.db.close()


def build_deps(settings: Settings) -> Deps:
    hours = (settings.business_hours_start, settings.business_hours_end, settings.business_day_set)
    if settings.mock_mode:
        sheets: SheetsBase = MemorySheetsRepo()
        calendar: CalendarBase = MemoryCalendar(settings.tz, *hours)
        gateway = MockSmsGateway()
        database_path = ":memory:"
    else:
        if not settings.google_sheet_id or not settings.google_calendar_id:
            raise RuntimeError("GOOGLE_SHEET_ID / GOOGLE_CALENDAR_ID 가 필요합니다. (테스트만 하려면 MOCK_MODE=true)")
        sheets = GoogleSheetsRepo(settings.google_service_account_file, settings.google_sheet_id)
        calendar = GoogleCalendarClient(
            settings.google_service_account_file, settings.google_calendar_id, settings.tz, *hours,
        )
        if settings.sms_provider == "smsgate":
            gateway = SmsGateway(
                settings.sms_gateway_base_url, settings.sms_gateway_username,
                settings.sms_gateway_password, settings.sms_gateway_signing_key,
            )
        else:
            gateway = MacroDroidGateway(settings.macrodroid_send_url, settings.macrodroid_send_key)
        database_path = settings.database_path

    return Deps(
        settings=settings,
        db=Database(database_path),
        sheets=sheets,
        calendar=calendar,
        gateway=gateway,
        notifier=OwnerNotifier(settings.telegram_bot_token, settings.telegram_chat_id, tz=settings.tz),
        claude=AsyncAnthropic(api_key=settings.anthropic_api_key),
    )
