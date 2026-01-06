from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from .models import Base

engine = None
SessionMaker: async_sessionmaker[AsyncSession] = None  # type: ignore


def init_db(dsn: str):
    global engine, SessionMaker
    engine = create_async_engine(dsn, echo=False, pool_pre_ping=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables():
    # MVP: создание таблиц без alembic
    async with engine.begin() as conn:  # type: ignore
        await conn.run_sync(Base.metadata.create_all)
