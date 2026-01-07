import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pytest_asyncio
from dotenv import load_dotenv

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from app.models import Base

load_dotenv()

TEST_DATABASE_DSN = "postgresql+asyncpg://postgres:postgres@localhost:5432/teacher_support_bot_test"
if not TEST_DATABASE_DSN:
    raise RuntimeError("Set TEST_DATABASE_DSN (recommended) or DATABASE_DSN")

if "test" not in TEST_DATABASE_DSN.lower():
    raise RuntimeError("Refusing to run tests on non-test database DSN (must contain 'test').")


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DATABASE_DSN, future=True, poolclass=NullPool)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(engine):
    # очищаем ВСЕ таблицы перед каждым тестом
    tables = [t.name for t in Base.metadata.sorted_tables]
    if not tables:
        return

    stmt = "TRUNCATE " + ", ".join(f'"{name}"' for name in tables) + " RESTART IDENTITY CASCADE;"
    async with engine.begin() as conn:
        await conn.execute(text(stmt))


@pytest_asyncio.fixture
async def sessionmaker(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def session(sessionmaker):
    async with sessionmaker() as s:
        yield s
