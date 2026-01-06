import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import settings
from .db import init_db, create_tables
from .middlewares import DbSessionMiddleware
from .handlers import routers
from .logging_conf import setup_logging


async def main():
    setup_logging()
    logging.getLogger(__name__).info("Starting bot...")
    init_db(settings.database_dsn)

    if settings.auto_create_tables == 1:
        await create_tables()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DbSessionMiddleware())

    for r in routers:
        dp.include_router(r)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
