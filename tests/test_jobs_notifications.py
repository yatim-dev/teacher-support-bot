from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from app.models import (
    Student, Lesson, LessonStatus,
    ParentStudent, Parent, User, Role,
    Notification, NotificationStatus,
)


def _freeze_datetime(monkeypatch, module, fixed: datetime):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDateTime)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


class FakeBotFail:
    async def send_message(self, tg_id: int, text: str):
        raise RuntimeError("TG error")


@pytest.mark.asyncio
async def test_plan_lesson_notifications_creates_24h_and_1h_for_student_and_parent(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    # student user + parent user
    student_user = User(tg_id=5001, role=Role.student, name="S", timezone="Europe/Moscow")
    parent_user = User(tg_id=5002, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add_all([student_user, parent_user])
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow", user_id=student_user.id)
    session.add(st)
    await session.flush()

    p = Parent(user_id=parent_user.id, full_name="Parent")
    session.add(p)
    await session.flush()

    session.add(ParentStudent(parent_id=p.id, student_id=st.id))

    lesson = Lesson(
        student_id=st.id,
        start_at=now + timedelta(days=2),  # обе напоминалки будут в будущем
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    await jobs.plan_lesson_notifications_job()

    async with sessionmaker() as s2:
        notifs = (await s2.execute(select(Notification))).scalars().all()
        assert len(notifs) == 4
        kinds = sorted({n.type for n in notifs})
        assert kinds == ["lesson_1h", "lesson_24h"]
        assert all(n.status == NotificationStatus.pending for n in notifs)
        assert all(n.entity_id == lesson.id for n in notifs)

    assert len(notifs) == 4  # 2 пользователя * (24h + 1h)

    kinds = sorted({n.type for n in notifs})
    assert kinds == ["lesson_1h", "lesson_24h"]

    assert all(n.status == NotificationStatus.pending for n in notifs)
    assert all(n.entity_id == lesson.id for n in notifs)


@pytest.mark.asyncio
async def test_plan_lesson_notifications_is_idempotent(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=5100, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow", user_id=u.id)
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=now + timedelta(days=2),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    await jobs.plan_lesson_notifications_job()
    await jobs.plan_lesson_notifications_job()

    cnt = (await session.execute(select(func.count()).select_from(Notification))).scalar_one()
    assert cnt == 2  # 1 пользователь * (24h + 1h)


@pytest.mark.asyncio
async def test_send_notifications_lesson_1h_marks_sent_and_sends_message(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=5200, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=now + timedelta(hours=2),  # урок в будущем, но уведомление "уже пора"
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="lesson_1h",
        entity_id=lesson.id,
        send_at=now - timedelta(minutes=1),  # <= now => должно отправиться
        payload=None,
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot, batch_size=50)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 5200
    assert "Напоминание: урок скоро" in bot.sent[0][1]

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.sent
        assert n2.last_error is None


@pytest.mark.asyncio
async def test_send_notifications_lesson_not_found_sets_failed(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=5555, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="lesson_1h",
        entity_id=999999,  # такого Lesson.id нет
        send_at=now - timedelta(seconds=1),  # пора отправлять
        payload=None,
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot)

    # сообщение не отправлено
    assert bot.sent == []

    # проверяем в НОВОЙ сессии (джоба работала в своей)
    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.failed
        assert n2.last_error  # там будет текст исключения типа "No row was found..."


@pytest.mark.asyncio
async def test_send_notifications_unknown_type_sets_failed(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=5300, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="unknown_type",
        entity_id=1,
        send_at=now - timedelta(seconds=1),
        payload=None,
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot)

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.failed
        assert "Unknown notification type" in (n2.last_error or "")


@pytest.mark.asyncio
async def test_send_notifications_bot_exception_sets_failed(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs

    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=5400, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=1,
        send_at=now - timedelta(seconds=1),
        payload="hello",
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBotFail()
    await jobs.send_notifications_job(bot)

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.failed
        assert n2.last_error
