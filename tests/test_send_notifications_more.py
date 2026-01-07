from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from app.models import (
    User, Role,
    Student, Lesson, LessonStatus,
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


@pytest.mark.asyncio
async def test_send_notifications_sends_only_due_not_future(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs
    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=8001, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    # due
    n1 = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=1,
        send_at=now - timedelta(seconds=1),
        payload="DUE",
        status=NotificationStatus.pending,
    )
    # future
    n2 = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=2,
        send_at=now + timedelta(minutes=10),
        payload="FUTURE",
        status=NotificationStatus.pending,
    )
    session.add_all([n1, n2])
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot, batch_size=50)

    assert bot.sent == [(8001, "DUE")]

    async with sessionmaker() as s2:
        s_n1 = (await s2.execute(select(Notification).where(Notification.id == n1.id))).scalar_one()
        s_n2 = (await s2.execute(select(Notification).where(Notification.id == n2.id))).scalar_one()
        assert s_n1.status == NotificationStatus.sent
        assert s_n2.status == NotificationStatus.pending


@pytest.mark.asyncio
async def test_send_notifications_respects_batch_size_and_order(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs
    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=8002, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    # создаём 3 due уведомления с разным send_at
    n_old = Notification(user_id=u.id, type="hw_graded", entity_id=1, send_at=now - timedelta(minutes=30),
                         payload="old", status=NotificationStatus.pending)
    n_mid = Notification(user_id=u.id, type="hw_graded", entity_id=2, send_at=now - timedelta(minutes=20),
                         payload="mid", status=NotificationStatus.pending)
    n_new = Notification(user_id=u.id, type="hw_graded", entity_id=3, send_at=now - timedelta(minutes=10),
                         payload="new", status=NotificationStatus.pending)
    session.add_all([n_new, n_mid, n_old])
    await session.commit()

    bot = FakeBot()

    await jobs.send_notifications_job(bot, batch_size=2)
    assert [t for _, t in bot.sent] == ["old", "mid"]

    await jobs.send_notifications_job(bot, batch_size=2)
    assert [t for _, t in bot.sent] == ["old", "mid", "new"]

    async with sessionmaker() as s2:
        cnt_sent = (await s2.execute(
            select(func.count()).select_from(Notification).where(Notification.status == NotificationStatus.sent)
        )).scalar_one()
        assert cnt_sent == 3


@pytest.mark.asyncio
async def test_send_notifications_hw_graded_without_payload_uses_default_text(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs
    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=8003, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=1,
        send_at=now - timedelta(seconds=1),
        payload=None,  # важно
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot, batch_size=50)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 8003
    assert "Выставлена оценка" in bot.sent[0][1]

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.sent


@pytest.mark.asyncio
async def test_send_notifications_lesson_24h_missing_lesson_sets_failed(monkeypatch, sessionmaker, session):
    """
    Реалистичная 'битая ссылка': entity_id без FK, урока может не быть -> except -> failed.
    """
    import app.jobs_notifications as jobs
    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=8004, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="lesson_24h",
        entity_id=999999,  # нет Lesson
        send_at=now - timedelta(seconds=1),
        payload=None,
        status=NotificationStatus.pending,
    )
    session.add(n)
    await session.commit()

    bot = FakeBot()
    await jobs.send_notifications_job(bot)

    assert bot.sent == []

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.failed
        assert n2.last_error


@pytest.mark.asyncio
async def test_send_notifications_unknown_type_sets_failed(monkeypatch, sessionmaker, session):
    import app.jobs_notifications as jobs
    monkeypatch.setattr(jobs.db, "SessionMaker", sessionmaker)

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_datetime(monkeypatch, jobs, now)

    u = User(tg_id=8005, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    n = Notification(
        user_id=u.id,
        type="weird_type",
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
