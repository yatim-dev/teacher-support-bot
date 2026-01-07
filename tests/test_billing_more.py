from datetime import datetime, timezone

import pytest
from sqlalchemy import select, func

from app.models import (
    Student, BillingMode,
    Lesson, LessonStatus,
    StudentBalance,
    LessonCharge, ChargeStatus,
    ParentStudent, Parent, User, Role,
)
from app.services.billing import mark_lesson_done, mark_charge_paid


class FakeBot:
    def __init__(self):
        self.sent = []  # list[(tg_id, text)]

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


@pytest.mark.asyncio
async def test_mark_lesson_done_single_is_idempotent(session):
    st = Student(
        full_name="Student",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=1000,
    )
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.flush()

    u = User(tg_id=2001, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    p = Parent(user_id=u.id, full_name="Parent")
    session.add(p)
    await session.flush()

    session.add(ParentStudent(parent_id=p.id, student_id=st.id))
    await session.commit()

    bot = FakeBot()

    charge_id_1 = await mark_lesson_done(session, bot, lesson.id)
    await session.commit()

    charge_id_2 = await mark_lesson_done(session, bot, lesson.id)
    await session.commit()

    assert isinstance(charge_id_1, int)
    assert charge_id_2 is None

    assert len(bot.sent) == 1

    cnt = (await session.execute(
        select(func.count()).select_from(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
    )).scalar_one()
    assert cnt == 1


@pytest.mark.asyncio
async def test_mark_lesson_done_subscription_is_idempotent(session):
    st = Student(
        full_name="Student",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        price_per_lesson=None,
    )
    session.add(st)
    await session.flush()

    bal = StudentBalance(student_id=st.id, lessons_left=1)
    session.add(bal)

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    r1 = await mark_lesson_done(session, bot, lesson.id)
    r2 = await mark_lesson_done(session, bot, lesson.id)

    assert r1 is None
    assert r2 is None
    assert bot.sent == []  # в subscription уведомлений нет

    bal_db = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == st.id)
    )).scalar_one()
    assert bal_db.lessons_left == 0


@pytest.mark.asyncio
async def test_mark_lesson_done_subscription_does_not_go_negative(session):
    st = Student(
        full_name="Student",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        price_per_lesson=None,
    )
    session.add(st)
    await session.flush()

    session.add(StudentBalance(student_id=st.id, lessons_left=0))

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    await mark_lesson_done(session, bot, lesson.id)

    assert bot.sent == []

    bal_db = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == st.id)
    )).scalar_one()
    assert bal_db.lessons_left == 0


@pytest.mark.asyncio
async def test_mark_charge_paid_does_not_set_paid_at_when_not_pending(session):
    # ВАЖНО: если у вас нет ChargeStatus.canceled — замените на любой НЕ pending статус.
    st = Student(full_name="A", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc),
        duration_min=60,
        status=LessonStatus.done,
    )
    session.add(lesson)
    await session.flush()

    ch = LessonCharge(
        lesson_id=lesson.id,
        student_id=st.id,
        amount=1000.0,
        status=ChargeStatus.canceled,
    )
    session.add(ch)
    await session.commit()

    await mark_charge_paid(session, ch.id)

    ch2 = (await session.execute(select(LessonCharge).where(LessonCharge.id == ch.id))).scalar_one()
    assert ch2.status == ChargeStatus.canceled
    assert ch2.paid_at is None
