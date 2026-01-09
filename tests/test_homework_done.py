import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.callbacks import HomeworkCb
from app.config import settings
from app.models import Homework, Lesson, LessonStatus, Role, Student, User

# поправьте импорт под ваш реальный модуль, где лежит handler homework_menu
from app.handlers.lesson_actions import homework_menu


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

    # Мокаем render_homework, чтобы тест не зависел от текста/клавиатур
    render_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "render_homework", render_mock)

    # чтобы текст уведомления был стабильнее (если используете форматирование дат)
    if hasattr(handler_mod, "fmt_dt_for_tz"):
        monkeypatch.setattr(handler_mod, "fmt_dt_for_tz", lambda dt, tz: "DATE")

    # пользователь-ученик
    u = User(tg_id=101, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(user_id=u.id, full_name="Student One", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()
    await session.refresh(st)

    lesson = Lesson(
        student_id=st.id,
        status=LessonStatus.planned,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)

    hw = Homework(lesson_id=lesson.id, title="HW", description="Desc", grade=None, student_done_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    call = FakeCall(tg_id=101)
    cb = HomeworkCb(action="done", lesson_id=lesson.id, student_id=st.id, offset=0)

    state = SimpleNamespace()
    await homework_menu(call, callback_data=cb, state=state, session=session)

    # 1) student_done_at проставился
    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.student_done_at is not None

    # 2) уведомление учителю отправилось 1 раз
    call.bot.send_message.assert_awaited_once()

    sent_call = call.bot.send_message.await_args  # _Call
    args = sent_call.args
    kwargs = sent_call.kwargs

    if kwargs:
        assert kwargs.get("chat_id") == 999999
        assert "Student One" in kwargs.get("text", "")
    else:
        assert args[0] == 999999
        assert "Student One" in args[1]

    # 3) карточка перерендерена
    render_mock.assert_awaited()

    # 4) call.answer вызван
    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_student_done_is_idempotent_no_second_notification(session, monkeypatch, handler_mod):
    monkeypatch.setattr(settings, "teacher_tg_id", 999999)
    monkeypatch.setattr(handler_mod, "render_homework", AsyncMock())
    if hasattr(handler_mod, "fmt_dt_for_tz"):
        monkeypatch.setattr(handler_mod, "fmt_dt_for_tz", lambda dt, tz: "DATE")

    u = User(tg_id=102, role=Role.student, name="S2", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(user_id=u.id, full_name="Student Two", timezone="Europe/Moscow")
    session.add(st)
    await session.commit()
    await session.refresh(st)

    lesson = Lesson(
        student_id=st.id,
        status=LessonStatus.planned,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)

    hw = Homework(lesson_id=lesson.id, title="HW2", description="Desc", grade=None, student_done_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    call = FakeCall(tg_id=102)
    cb = HomeworkCb(action="done", lesson_id=lesson.id, student_id=st.id, offset=0)
    state = SimpleNamespace()

    # 1-й раз: уведомление должно уйти
    await homework_menu(call, callback_data=cb, state=state, session=session)
    assert call.bot.send_message.await_count == 1

    # 2-й раз: уведомление НЕ должно уйти повторно
    await homework_menu(call, callback_data=cb, state=state, session=session)
    assert call.bot.send_message.await_count == 1  # не увеличилось


@pytest.mark.asyncio
async def test_student_done_denied_for_foreign_lesson(session, monkeypatch, handler_mod):
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

    # ученик B + его урок
    u2 = User(tg_id=202, role=Role.student, name="B", timezone="Europe/Moscow")
    session.add(u2)
    await session.commit()
    await session.refresh(u2)

    st2 = Student(user_id=u2.id, full_name="B", timezone="Europe/Moscow")
    session.add(st2)
    await session.commit()
    await session.refresh(st2)

    lesson_b = Lesson(
        student_id=st2.id,
        status=LessonStatus.planned,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session.add(lesson_b)
    await session.commit()
    await session.refresh(lesson_b)

    hw = Homework(lesson_id=lesson_b.id, title="HW", description="Desc", grade=None, student_done_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    # A пытается отметить выполненным ДЗ урока B
    call = FakeCall(tg_id=201)
    cb = HomeworkCb(action="done", lesson_id=lesson_b.id, student_id=st1.id, offset=0)
    state = SimpleNamespace()

    await homework_menu(call, callback_data=cb, state=state, session=session)

    # должно отказать с show_alert=True
    call.answer.assert_awaited()
    answered = call.answer.await_args
    assert answered.kwargs.get("show_alert") is True

    # уведомления не было
    call.bot.send_message.assert_not_awaited()

    # student_done_at не проставился
    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.student_done_at is None
