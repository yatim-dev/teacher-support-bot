import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.callbacks import HomeworkCb
from app.config import settings
from app.models import Homework, Lesson, LessonStatus, Role, Student, User

from app.handlers.admin.homeworks import homework_menu


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text: str, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCall:
    def __init__(self, tg_id: int):
        self.from_user = SimpleNamespace(id=tg_id)
        self.message = FakeMessage()
        self.answer = AsyncMock()
        self.bot = SimpleNamespace(send_message=AsyncMock())


@pytest.fixture
def handler_mod():
    """Модуль, где определён homework_menu (для monkeypatch без хардкода путей)."""
    return sys.modules[homework_menu.__module__]


@pytest.mark.asyncio
async def test_student_done_sets_student_done_at_and_notifies_teacher(session, monkeypatch, handler_mod):
    monkeypatch.setattr(settings, "teacher_tg_id", 999999)

    render_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "render_homework", render_mock)

    # пользователь-ученик
    u = User(tg_id=101, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(user_id=u.id, full_name="Student One", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()
    await session.refresh(st)

    # ДЗ теперь не связано с Lesson
    hw = Homework(
        student_id=st.id,
        title="HW",
        description="Desc",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    call = FakeCall(tg_id=101)
    cb = HomeworkCb(action="done", homework_id=hw.id, student_id=st.id, offset=0)

    state = SimpleNamespace()
    await homework_menu(call, callback_data=cb, state=state, session=session)

    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.student_done_at is not None

    call.bot.send_message.assert_awaited_once()
    sent_call = call.bot.send_message.await_args
    args = sent_call.args
    kwargs = sent_call.kwargs

    if kwargs:
        assert kwargs.get("chat_id") == 999999
        assert "Student One" in kwargs.get("text", "")
    else:
        assert args[0] == 999999
        assert "Student One" in args[1]

    render_mock.assert_awaited()
    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_student_done_is_idempotent_no_second_notification(session, monkeypatch, handler_mod):
    monkeypatch.setattr(settings, "teacher_tg_id", 999999)
    monkeypatch.setattr(handler_mod, "render_homework", AsyncMock())

    u = User(tg_id=102, role=Role.student, name="S2", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(user_id=u.id, full_name="Student Two", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()
    await session.refresh(st)

    hw = Homework(
        student_id=st.id,
        title="HW2",
        description="Desc",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    call = FakeCall(tg_id=102)
    cb = HomeworkCb(action="done", homework_id=hw.id, student_id=st.id, offset=0)
    state = SimpleNamespace()

    # 1-й раз: уведомление должно уйти
    await homework_menu(call, callback_data=cb, state=state, session=session)
    assert call.bot.send_message.await_count == 1

    # 2-й раз: уведомление НЕ должно уйти повторно
    await homework_menu(call, callback_data=cb, state=state, session=session)
    assert call.bot.send_message.await_count == 1  # не увеличилось


@pytest.mark.asyncio
async def test_student_done_denied_for_foreign_homework(session, monkeypatch, handler_mod):
    monkeypatch.setattr(settings, "teacher_tg_id", 999999)
    monkeypatch.setattr(handler_mod, "render_homework", AsyncMock())

    # ученик A
    u1 = User(tg_id=201, role=Role.student, name="A", timezone="Europe/Moscow")
    session.add(u1)
    await session.commit()
    await session.refresh(u1)

    st1 = Student(user_id=u1.id, full_name="A", timezone="Europe/Moscow")
    session.add(st1)
    await session.commit()
    await session.refresh(st1)

    # ученик B + его ДЗ
    u2 = User(tg_id=202, role=Role.student, name="B", timezone="Europe/Moscow")
    session.add(u2)
    await session.commit()
    await session.refresh(u2)

    st2 = Student(user_id=u2.id, full_name="B", timezone="Europe/Moscow")
    session.add(st2)
    await session.commit()
    await session.refresh(st2)

    hw_b = Homework(
        student_id=st2.id,
        title="HW",
        description="Desc",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw_b)
    await session.commit()
    await session.refresh(hw_b)

    # A пытается отметить выполненным ДЗ ученика B
    call = FakeCall(tg_id=201)
    cb = HomeworkCb(action="done", homework_id=hw_b.id, student_id=st1.id, offset=0)
    state = SimpleNamespace()

    await homework_menu(call, callback_data=cb, state=state, session=session)

    # должно отказать с show_alert=True
    call.answer.assert_awaited()
    answered = call.answer.await_args
    assert answered.kwargs.get("show_alert") is True

    # уведомления не было
    call.bot.send_message.assert_not_awaited()

    # student_done_at не проставился
    hw2 = (await session.execute(select(Homework).where(Homework.id == hw_b.id))).scalar_one()
    assert hw2.student_done_at is None
