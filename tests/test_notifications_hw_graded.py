import pytest
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import User, Notification, NotificationStatus, Role
from app.jobs_notifications import send_notifications_job


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


@pytest.mark.asyncio
async def test_send_notifications_hw_graded_marks_sent(monkeypatch, sessionmaker, session):
    # создаём user
    u = User(tg_id=111, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    # создаём нотификацию hw_graded
    payload = "Оценка: 10/10"
    n = Notification(
        user_id=u.id,
        type="hw_graded",
        entity_id=123,
        send_at=datetime.now(timezone.utc),
        payload=payload,
        status=NotificationStatus.pending,
        last_error=None
    )
    session.add(n)
    await session.commit()

    # подменяем SessionMaker в jobs_notifications на тестовый
    from app import jobs_notifications
    monkeypatch.setattr(jobs_notifications.db, "SessionMaker", sessionmaker)

    bot = FakeBot()
    await send_notifications_job(bot, batch_size=50)

    # бот отправил сообщение
    assert bot.sent == [(111, payload)]

    # статус стал sent
    async with sessionmaker() as s2:
        n2 = (await s2.execute(select(Notification).where(Notification.id == n.id))).scalar_one()
        assert n2.status == NotificationStatus.sent
        assert n2.last_error is None
