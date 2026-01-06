import asyncio
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .db import init_db
from .logging_conf import setup_logging
from .jobs_lessons import generate_lessons_job
from .jobs_notifications import plan_lesson_notifications_job, send_notifications_job


async def main():
    setup_logging()
    log = logging.getLogger(__name__)
    log.info("Starting worker...")

    init_db(settings.database_dsn)

    bot = Bot(token=settings.bot_token)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(generate_lessons_job, "interval", hours=24)
    scheduler.add_job(plan_lesson_notifications_job, "interval", minutes=30)

    async def _send_notifs():
        await send_notifications_job(bot)

    scheduler.add_job(_send_notifs, "cron", second=0)
    scheduler.start()
    log.info("Worker scheduler started")

    # держим процесс живым
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
