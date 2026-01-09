# tests/test_menu_flow.py
import pytest
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup

from app.models import User, Role

from types import SimpleNamespace
# поправьте путь под ваш проект:
from app.handlers.menu import show_menu, menu_inline

def fake_message():
    m = SimpleNamespace()
    m.answer = AsyncMock()
    m.edit_text = AsyncMock()
    return m


def fake_call(tg_id: int = 123):
    c = SimpleNamespace()
    c.from_user = SimpleNamespace(id=tg_id)
    c.message = fake_message()
    c.answer = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_show_menu_no_timezone_answers_tz(monkeypatch):
    msg = fake_message()

    sentinel_tz = InlineKeyboardMarkup(inline_keyboard=[])
    monkeypatch.setattr("app.handlers.menu.tz_kb", lambda: sentinel_tz)

    user = User(tg_id=1, role=Role.student, timezone=None)

    await show_menu(msg, session=None, user=user, edit=False)

    msg.answer.assert_awaited_once()
    args, kwargs = msg.answer.await_args
    assert "Сначала выберите часовой пояс" in args[0]
    assert kwargs["reply_markup"] is sentinel_tz


@pytest.mark.asyncio
async def test_show_menu_edit_text_when_timezone_set(monkeypatch):
    msg = fake_message()

    sentinel_menu = InlineKeyboardMarkup(inline_keyboard=[])
    monkeypatch.setattr("app.handlers.menu.main_menu", lambda role: sentinel_menu)

    user = User(tg_id=1, role=Role.student, timezone="Europe/Moscow")

    await show_menu(msg, session=None, user=user, edit=True)

    msg.edit_text.assert_awaited_once()
    args, kwargs = msg.edit_text.await_args
    assert "Меню (Ученик)" in args[0]
    assert kwargs["reply_markup"] is sentinel_menu


@pytest.mark.asyncio
async def test_menu_inline_opens_same_menu_as_menu_command(session, monkeypatch):
    """
    Реальная БД: создаём пользователя и проверяем, что callback MenuCb(section="menu")
    приводит к edit_text с меню.
    """
    # Создаём пользователя в БД
    u = User(tg_id=123, role=Role.student, timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    sentinel_menu = InlineKeyboardMarkup(inline_keyboard=[])
    monkeypatch.setattr("app.handlers.menu.main_menu", lambda role: sentinel_menu)

    call = fake_call(tg_id=123)

    await menu_inline(call, session=session)

    call.message.edit_text.assert_awaited_once()
    args, kwargs = call.message.edit_text.await_args
    assert "Меню (Ученик)" in args[0]
    assert kwargs["reply_markup"] is sentinel_menu
    call.answer.assert_awaited_once()
