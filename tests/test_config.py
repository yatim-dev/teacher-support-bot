import pytest
from pydantic import ValidationError
from app.config import Settings

def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DATABASE_DSN", "postgresql+asyncpg://postgres:postgres@localhost:5432/teacher_support_bot_test")
    monkeypatch.setenv("TEACHER_TG_ID", "777")
    monkeypatch.delenv("AUTO_CREATE_TABLES", raising=False)

    s = Settings(_env_file=None)  # игнорируем .env
    assert s.auto_create_tables == 0

def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_DSN", raising=False)
    monkeypatch.delenv("TEACHER_TG_ID", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
