from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils import normalize_phone


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    business_name: str = "OO업체"
    timezone: str = "Asia/Seoul"
    business_hours_start: int = 9
    business_hours_end: int = 18
    business_days: str = "0,1,2,3,4,5"
    consult_minutes: int = 60

    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_max_tokens: int = 1024

    sms_provider: str = "macrodroid"
    macrodroid_send_url: str = ""
    macrodroid_send_key: str = ""

    sms_gateway_base_url: str = "https://api.sms-gate.app/3rdparty/v1"
    sms_gateway_username: str = ""
    sms_gateway_password: str = ""
    sms_gateway_signing_key: str = ""

    webhook_token: str

    google_service_account_file: str = "credentials/service-account.json"
    google_sheet_id: str = ""
    google_calendar_id: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    database_path: str = "data/agent.db"
    batch_window_seconds: int = 45
    max_replies_per_hour: int = 6
    history_limit: int = 20
    reminder_hour: int = 18
    report_hour: int = 21
    dry_run: bool = True
    test_numbers: str = ""
    mock_mode: bool = False
    missed_call_notice_senders: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def business_day_set(self) -> set[int]:
        return {int(d) for d in self.business_days.split(",") if d.strip()}

    @property
    def test_number_set(self) -> set[str]:
        return {normalize_phone(n) for n in self.test_numbers.split(",") if n.strip()}

    @property
    def missed_call_notice_sender_set(self) -> set[str]:
        return {normalize_phone(n) for n in self.missed_call_notice_senders.split(",") if n.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
