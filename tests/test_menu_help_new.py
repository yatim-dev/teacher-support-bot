from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import User, Role
from app.callbacks import MenuCb, TzCb


class FakeMessage:
    def __init__(self, tg_id: int):
        self.from_user = SimpleNamespace(id=tg_id)
        self.answers = []  # (text, reply_markup)
        self.edits = []    # (text, reply_markup)

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallbackQuery:
    def __init__(self, tg_id: int, message: FakeMessage):
        self.from_user = SimpleNamespace(id=tg_id)
        self.message = message
        self.answer = AsyncMock()


@pytest.fixture
def menu_mod():
    import app.handlers.menu as m
    return m


@pytest.mark.asyncio
async def test_help_cmd_unregistered_user_shows_register_hint(session, menu_mod):
    msg = FakeMessage(tg_id=10001)

    await menu_mod.help_cmd(msg, session)

    assert msg.answers
    text, _ = msg.answers[0]
    assert "Помощь" in text
    assert "Сначала зарегистрируйтесь" in text


@pytest.mark.asyncio
async def test_help_cmd_teacher_text(session, menu_mod):
    u = User(tg_id=10002, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    msg = FakeMessage(tg_id=10002)
    await menu_mod.help_cmd(msg, session)

    assert msg.answers
    text, _ = msg.answers[0]
    assert "Учитель" in text
    assert "Админка" in text


@pytest.mark.asyncio
async def test_help_inline_student_edits_text_and_answers(session, monkeypatch, menu_mod):
    # Чтобы тест не зависел от реальной клавиатуры
    monkeypatch.setattr(menu_mod, "main_menu", lambda role: f"MAIN_MENU<{role}>")

    u = User(tg_id=10003, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    msg = FakeMessage(tg_id=10003)
    call = FakeCallbackQuery(tg_id=10003, message=msg)

    cb = MenuCb(section="help")
    await menu_mod.help_inline(call, session)

    assert msg.edits
    text, markup = msg.edits[0]
    assert "Ученик" in text
    assert "Задание выполнено" in text  # если оставили этот пункт в help_text()
    assert markup == "MAIN_MENU<student>"

    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_menu_inline_requires_timezone_shows_tz_kb(session, monkeypatch, menu_mod):
    tz_markup = object()
    monkeypatch.setattr(menu_mod, "tz_kb", lambda: tz_markup)

    # main_menu не нужен: ветка уйдёт в выбор TZ
    u = User(tg_id=10004, role=Role.parent, name="P", timezone=None)
    session.add(u)
    await session.commit()

    msg = FakeMessage(tg_id=10004)
    call = FakeCallbackQuery(tg_id=10004, message=msg)

    cb = MenuCb(section="menu")
    await menu_mod.menu_inline(call, session)

    assert msg.edits
    text, markup = msg.edits[0]
    assert "Сначала выберите часовой пояс" in text
    assert markup is tz_markup

    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_tz_set_updates_user_timezone_and_shows_menu(session, monkeypatch, menu_mod):
    # Чтобы тест не зависел от реальной клавиатуры
    monkeypatch.setattr(menu_mod, "main_menu", lambda role: f"MAIN_MENU<{role}>")

    u = User(tg_id=10005, role=Role.student, name="S5", timezone=None)
    session.add(u)
    await session.commit()

    msg = FakeMessage(tg_id=10005)
    call = FakeCallbackQuery(tg_id=10005, message=msg)

    cb = TzCb(value="Europe/Moscow")
    await menu_mod.tz_set(call, cb, session)

    # 1) timezone обновился в БД
    u2 = (await session.execute(select(User).where(User.tg_id == 10005))).scalar_one()
    assert u2.timezone == "Europe/Moscow"

    # 2) показали меню (edit_text был)
    assert msg.edits
    text, markup = msg.edits[0]
    assert "Меню (Ученик)" in text
    assert markup == "MAIN_MENU<student>"

    # 3) call.answer содержит подтверждение
    call.answer.assert_awaited()
    sent = call.answer.await_args
    assert sent.args  # должен быть текст первым позиционным аргументом
    assert "Часовой пояс установлен: Europe/Moscow" == sent.args[0]
