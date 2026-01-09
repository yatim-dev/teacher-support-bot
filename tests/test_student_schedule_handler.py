# tests/test_student_schedule_handler.py
import sys
from unittest.mock import AsyncMock

import pytest
from datetime import datetime, timezone, timedelta

from app.models import User, Student, Lesson, Role, LessonStatus
from types import SimpleNamespace
# ВАЖНО: поправьте этот импорт под ваш реальный файл, где лежит хендлер
from app.handlers.student import student_schedule

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


def _handler_module():
    # модуль, где определён student_schedule (без хардкода пути)
    return sys.modules[student_schedule.__module__]


@pytest.mark.asyncio
async def test_student_schedule_denied_for_non_student(session):
    u = User(tg_id=123, role=Role.teacher, timezone="Europe/Moscow")
    session.add(u)
    await session.commit()

    call = fake_call(tg_id=123)

    await student_schedule(call, session=session)

    call.answer.assert_awaited_once()
    _, kwargs = call.answer.await_args
    assert kwargs.get("show_alert") is True
    call.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_student_schedule_no_lessons(session, monkeypatch):
    mod = _handler_module()

    u = User(tg_id=123, role=Role.student, timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(
        user_id=u.id,
        full_name="Иван Иванов",
        board_url="https://example.com",
        timezone="Europe/Moscow",
    )
    session.add(st)
    await session.commit()

    async def fake_avg(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "homework_avg_last_n", fake_avg)

    call = fake_call(tg_id=123)

    await student_schedule(call, session=session)

    call.message.edit_text.assert_awaited_once()
    args, kwargs = call.message.edit_text.await_args
    text = args[0]

    assert "На ближайшие 7 дней уроков нет" in text
    assert "Средняя оценка ДЗ" in text
    assert kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_student_schedule_with_lessons_has_homework_kb(session, monkeypatch):
    mod = _handler_module()

    u = User(tg_id=123, role=Role.student, timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    await session.refresh(u)

    st = Student(
        user_id=u.id,
        full_name="Иван Иванов",
        board_url="https://example.com",
        timezone="Europe/Moscow",
    )
    session.add(st)
    await session.commit()
    await session.refresh(st)

    now = datetime.now(timezone.utc)
    l1 = Lesson(student_id=st.id, status=LessonStatus.planned, start_at=now + timedelta(hours=1))
    l2 = Lesson(student_id=st.id, status=LessonStatus.planned, start_at=now + timedelta(hours=2))
    session.add_all([l1, l2])
    await session.commit()

    async def fake_avg(*args, **kwargs):
        return 7.5

    monkeypatch.setattr(mod, "homework_avg_last_n", fake_avg)
    monkeypatch.setattr(mod, "fmt_dt_for_tz", lambda dt, tz: "2026-01-10 18:30")

    call = fake_call(tg_id=123)

    await student_schedule(call, session=session)

    call.message.edit_text.assert_awaited_once()
    args, kwargs = call.message.edit_text.await_args
    text = args[0]

    assert "Ваши уроки (7 дней)" in text
    assert "Нажмите «ДЗ»" in text
    assert "Средняя оценка ДЗ" in text

    markup = kwargs.get("reply_markup")
    assert markup is not None
    assert markup.inline_keyboard[-1][0].text == "Назад"
