from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.service import ConversationService


def build_scheduler(service: ConversationService, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    common = {"max_instances": 1, "coalesce": True}
    scheduler.add_job(service.send_ready_quotes, "interval", minutes=2, id="ready_quotes", **common)
    scheduler.add_job(service.send_reminders, "cron", hour=settings.reminder_hour, id="reminders", **common)
    scheduler.add_job(service.daily_report, "cron", hour=settings.report_hour, id="daily_report", **common)
    return scheduler
