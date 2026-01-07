from __future__ import annotations

import pytest
from sqlalchemy import select
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from app.models import User, Role
from app.callbacks import MenuCb, AdminCb, TzCb


class FakeFromUser:
    def __init__(self, user_id: int, full_name: str):
        self.id = user_id
        self.full_name = full_name


class FakeMessage:
    def __init__(self, from_user: FakeFromUser, text: str | None = None):
        self.from_user = from_user
        self.text = text
        self.answers: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup | None]] = []

        # опционально: заставить edit_text бросать исключение
        self.raise_on_edit: Exception | None = None

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup=None):
        if self.raise_on_edit:
            raise self.raise_on_edit
        self.edits.append((text, reply_markup))


class FakeCallbackQuery:
    def __init__(self, from_user: FakeFromUser, message: FakeMessage):
        self.from_user = from_user
        self.message = message
        self.answered = 0

    async def answer(self):
        self.answered += 1


class FakeFSMContext:
    def __init__(self):
        self.state = None
        self.cleared = False

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.cleared = True
        self.state = None


@pytest.mark.asyncio
async def test_start_teacher_flow(monkeypatch, session):
    """
    /start: если tg_id == settings.teacher_tg_id -> ensure_teacher_user создаёт/находит учителя,
    и хендлер пишет "Вы вошли как учитель..."
    """
    import app.handlers.start as start_mod

    # teacher_tg_id совпадает с tg_id
    monkeypatch.setattr(start_mod.settings, "teacher_tg_id", 9001)

    msg = FakeMessage(FakeFromUser(9001, "Teacher"))
    state = FakeFSMContext()

    await start_mod.start(msg, state, session)

    assert msg.answers
    assert "Вы вошли как учитель" in msg.answers[0][0]
    assert state.state is None  # state не ставится


@pytest.mark.asyncio
async def test_start_already_registered_without_timezone_asks_tz(session):
    import app.handlers.start as start_mod

    # в БД уже есть пользователь без timezone
    u = User(tg_id=9002, role=Role.parent, name="P", timezone=None)
    session.add(u)
    await session.commit()

    msg = FakeMessage(FakeFromUser(9002, "Parent"), text="/start")
    state = FakeFSMContext()

    await start_mod.start(msg, state, session)

    # сначала попросит TZ, потом скажет "Вы уже зарегистрированы"
    assert len(msg.answers) >= 2
    assert "Выберите ваш часовой пояс" in msg.answers[0][0]
    assert msg.answers[0][1] is not None
    assert "Вы уже зарегистрированы" in msg.answers[1][0]
    assert state.state is None


@pytest.mark.asyncio
async def test_start_new_user_sets_waiting_key_state(session):
    import app.handlers.start as start_mod

    msg = FakeMessage(FakeFromUser(9003, "New User"), text="/start")
    state = FakeFSMContext()

    await start_mod.start(msg, state, session)

    assert state.state == start_mod.Reg.waiting_key
    assert msg.answers[-1][0] == "Введите ключ регистрации:"


@pytest.mark.asyncio
async def test_process_key_invalid_key_does_not_clear_state(monkeypatch, session):
    import app.handlers.start as start_mod

    async def fake_register_by_key(**kwargs):
        return False, "Ключ недействителен."

    monkeypatch.setattr(start_mod, "register_by_key", fake_register_by_key)

    msg = FakeMessage(FakeFromUser(9004, "X"), text="BADKEY")
    state = FakeFSMContext()
    state.state = start_mod.Reg.waiting_key

    await start_mod.process_key(msg, state, session)

    assert state.cleared is False
    assert msg.answers[-1][0] == "Ключ недействителен."


@pytest.mark.asyncio
async def test_process_key_success_clears_state_and_shows_tz(monkeypatch, session):
    import app.handlers.start as start_mod

    async def fake_register_by_key(**kwargs):
        return True, "Регистрация завершена."

    monkeypatch.setattr(start_mod, "register_by_key", fake_register_by_key)

    msg = FakeMessage(FakeFromUser(9005, "X"), text="OKKEY")
    state = FakeFSMContext()
    state.state = start_mod.Reg.waiting_key

    await start_mod.process_key(msg, state, session)

    assert state.cleared is True
    # 3 сообщения: текст регистрации + TZ + "Дальше: /menu"
    assert len(msg.answers) == 3
    assert msg.answers[0][0] == "Регистрация завершена."
    assert "Теперь выберите ваш часовой пояс" in msg.answers[1][0]
    assert msg.answers[1][1] is not None
    assert msg.answers[2][0] == "Дальше: /menu"


@pytest.mark.asyncio
async def test_menu_without_timezone_requests_tz(session):
    import app.handlers.menu as menu_mod

    u = User(tg_id=9100, role=Role.parent, name="P", timezone=None)
    session.add(u)
    await session.commit()

    msg = FakeMessage(FakeFromUser(9100, "P"), text="/menu")

    await menu_mod.menu(msg, session)

    assert msg.answers
    assert "Сначала выберите часовой пояс" in msg.answers[0][0]
    assert msg.answers[0][1] is not None


@pytest.mark.asyncio
async def test_menu_teacher_with_timezone_shows_admin_button(session):
    import app.handlers.menu as menu_mod

    u = User(tg_id=9101, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    msg = FakeMessage(FakeFromUser(9101, "T"), text="/menu")

    await menu_mod.menu(msg, session)

    assert msg.answers
    text, kb = msg.answers[0]
    assert "Меню (Учитель)" in text
    assert kb is not None

    # проверим, что в клавиатуре есть кнопка "Админка"
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    cbs = [btn.callback_data for btn in all_buttons]
    assert MenuCb(section="admin").pack() in cbs


@pytest.mark.asyncio
async def test_tz_set_updates_user_timezone(session):
    import app.handlers.menu as menu_mod

    u = User(tg_id=9102, role=Role.parent, name="P", timezone=None)
    session.add(u)
    await session.commit()

    msg = FakeMessage(FakeFromUser(9102, "P"))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = TzCb(value="Europe/Moscow")
    await menu_mod.tz_set(call, cb, session)

    # edit_text был вызван
    assert msg.edits
    assert "Часовой пояс установлен: Europe/Moscow" in msg.edits[0][0]
    assert call.answered == 1

    # timezone реально обновился в БД
    u2 = (await session.execute(select(User).where(User.tg_id == 9102))).scalar_one()
    assert u2.timezone == "Europe/Moscow"


@pytest.mark.asyncio
async def test_help_inline_ignores_message_not_modified(session):
    import app.handlers.menu as menu_mod

    u = User(tg_id=9103, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    msg = FakeMessage(FakeFromUser(9103, "P"))
    msg.raise_on_edit = TelegramBadRequest(method="editMessageText", message="message is not modified")
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    # не должно упасть
    await menu_mod.help_inline(call, session)
    assert call.answered == 1
