from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import User, Notification, NotificationStatus, Role
from app.jobs_notifications import send_notifications_job


class FakeBotOK:
    def __init__(self):
        self.sent = []

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


class FakeBotFail:
    async def send_message(self, tg_id: int, text: str):
        raise RuntimeError("telegram is down")


@pytest.mark.asyncio
async def test_send_notifications_sends_only_due_pending(monkeypatch, sessionmaker, session):
    u = User(tg_id=111, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    now = datetime.now(timezone.utc)

    due_pending = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=1,
        send_at=now - timedelta(seconds=5),
        payload="due",
        status=NotificationStatus.pending,
        last_error=None,
    )
    future_pending = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=2,
        send_at=now + timedelta(days=1),
        payload="future",
        status=NotificationStatus.pending,
        last_error=None,
    )
    already_sent = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=3,
        send_at=now - timedelta(days=1),
        payload="sent",
        status=NotificationStatus.sent,
        last_error=None,
    )

    session.add_all([due_pending, future_pending, already_sent])
    await session.commit()

    from app import jobs_notifications
    monkeypatch.setattr(jobs_notifications.db, "SessionMaker", sessionmaker)

    bot = FakeBotOK()
    await send_notifications_job(bot, batch_size=50)

    assert bot.sent == [(111, "due")]

    async with sessionmaker() as s2:
        n_due = (await s2.execute(select(Notification).where(Notification.id == due_pending.id))).scalar_one()
        n_future = (await s2.execute(select(Notification).where(Notification.id == future_pending.id))).scalar_one()

        assert n_due.status == NotificationStatus.sent
        assert n_future.status == NotificationStatus.pending


@pytest.mark.asyncio
async def test_send_notifications_respects_batch_size(monkeypatch, sessionmaker, session):
    u = User(tg_id=222, role=Role.parent, name="P2", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    now = datetime.now(timezone.utc)

    session.add_all([
        Notification(user_id=u.id, type="hw_graded", entity_id=10, send_at=now - timedelta(minutes=1), payload="n1",
                     status=NotificationStatus.pending, last_error=None),
        Notification(user_id=u.id, type="hw_graded", entity_id=11, send_at=now - timedelta(minutes=1), payload="n2",
                     status=NotificationStatus.pending, last_error=None),
        Notification(user_id=u.id, type="hw_graded", entity_id=12, send_at=now - timedelta(minutes=1), payload="n3",
                     status=NotificationStatus.pending, last_error=None),
    ])
    await session.commit()

    from app import jobs_notifications
    monkeypatch.setattr(jobs_notifications.db, "SessionMaker", sessionmaker)

    bot = FakeBotOK()
    await send_notifications_job(bot, batch_size=2)

    assert len(bot.sent) == 2  # отправлено только 2

    # второй прогон — должен добить остаток (идемпотентность + batch)
    await send_notifications_job(bot, batch_size=2)
    assert len(bot.sent) == 3


@pytest.mark.asyncio
async def test_send_notifications_on_bot_error_sets_last_error(monkeypatch, sessionmaker, session):
    u = User(tg_id=333, role=Role.parent, name="P3", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    now = datetime.now(timezone.utc)

    n = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=99,
        send_at=now - timedelta(seconds=1),
        payload="will fail",
        status=NotificationStatus.pending,
        last_error=None,
    )
    session.add(n)
    await session.commit()

    from app import jobs_notifications
    monkeypatch.setattr(jobs_notifications.db, "SessionMaker", sessionmaker)

    bot = FakeBotFail()
    await send_notifications_job(bot, batch_size=50)

    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.last_error is not None

        # ВАЖНО: подстройте под вашу реализацию:
        # если у вас есть статус failed/error — ожидаем его,
        # иначе может остаться pending (но с last_error / attempts).
        assert n2.status != NotificationStatus.sent
        # например, если есть:
        # assert n2.status == NotificationStatus.failed
