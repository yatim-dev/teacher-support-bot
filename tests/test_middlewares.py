import pytest
from types import SimpleNamespace

from app.middlewares import DbSessionMiddleware


@pytest.mark.asyncio
async def test_db_session_middleware_injects_session(monkeypatch, sessionmaker):
    import app.middlewares as mw_module

    # подменяем SessionMaker в app.db, который импортируется как mw_module.db
    monkeypatch.setattr(mw_module.db, "SessionMaker", sessionmaker)

    mw = DbSessionMiddleware()

    async def handler(event, data):
        assert "session" in data
        # session должен быть AsyncSession
        assert hasattr(data["session"], "execute")
        return "ok"

    res = await mw(handler, event=SimpleNamespace(), data={})
    assert res == "ok"
