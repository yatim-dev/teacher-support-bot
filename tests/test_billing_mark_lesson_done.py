from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, func

from app.models import (
    Lesson, LessonStatus, Student, BillingMode,
    StudentBalance, LessonCharge, ChargeStatus,
    ParentStudent, Parent, User, Role
)
from app.services.billing import mark_lesson_done


class FakeBot:
    def __init__(self):
        self.sent = []  # list[(tg_id, text)]

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


@pytest.mark.asyncio
async def test_mark_lesson_done_subscription_creates_balance_if_missing_and_does_not_create_charge(session):
    st = Student(
        full_name="A",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        price_per_lesson=None,
    )
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    res = await mark_lesson_done(session, bot, lesson.id)

    assert res is None
    assert bot.sent == []

    # урок стал done
    lesson_db = (await session.execute(select(Lesson).where(Lesson.id == lesson.id))).scalar_one()
    assert lesson_db.status == LessonStatus.done
    assert lesson_db.done_at is not None

    # баланс создан, но не уходит в минус
    bal = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == st.id)
    )).scalar_one()
    assert bal.lessons_left == 0

    # начислений нет
    cnt = (await session.execute(
        select(func.count()).select_from(LessonCharge).where(LessonCharge.student_id == st.id)
    )).scalar_one()
    assert cnt == 0


@pytest.mark.asyncio
async def test_mark_lesson_done_subscription_decrements_lessons_left(session):
    st = Student(
        full_name="A",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        price_per_lesson=None,
    )
    session.add(st)
    await session.flush()

    bal = StudentBalance(student_id=st.id, lessons_left=2)
    session.add(bal)

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    await mark_lesson_done(session, bot, lesson.id)

    bal_db = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == st.id)
    )).scalar_one()
    assert bal_db.lessons_left == 1


@pytest.mark.asyncio
async def test_mark_lesson_done_single_creates_charge_and_notifies_parents(session):
    # если у вас enum называется иначе — замените BillingMode.single на нужное значение
    st = Student(
        full_name="Student",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=1500,
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

    # два родителя (User) + Parent + связь ParentStudent
    u1 = User(tg_id=1001, role=Role.parent, name="P1", timezone="Europe/Moscow")
    u2 = User(tg_id=1002, role=Role.parent, name="P2", timezone="Europe/Moscow")
    session.add_all([u1, u2])
    await session.flush()

    p1 = Parent(user_id=u1.id, full_name="Parent 1")
    p2 = Parent(user_id=u2.id, full_name="Parent 2")
    session.add_all([p1, p2])
    await session.flush()

    session.add_all([
        ParentStudent(parent_id=p1.id, student_id=st.id),
        ParentStudent(parent_id=p2.id, student_id=st.id),
    ])
    await session.commit()

    bot = FakeBot()
    charge_id = await mark_lesson_done(session, bot, lesson.id)

    assert isinstance(charge_id, int)
    assert len(bot.sent) == 2
    assert {tg for tg, _ in bot.sent} == {1001, 1002}
    assert all("К оплате:" in text for _, text in bot.sent)

    # проверим начисление
    ch = (await session.execute(select(LessonCharge).where(LessonCharge.id == charge_id))).scalar_one()
    assert ch.lesson_id == lesson.id
    assert ch.student_id == st.id
    assert ch.status == ChargeStatus.pending
    assert ch.amount == float(st.price_per_lesson)

    # урок стал done
    lesson_db = (await session.execute(select(Lesson).where(Lesson.id == lesson.id))).scalar_one()
    assert lesson_db.status == LessonStatus.done
    assert lesson_db.done_at is not None


@pytest.mark.asyncio
async def test_mark_lesson_done_returns_none_if_not_planned(session):
    st = Student(full_name="A", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc),
        duration_min=60,
        status=LessonStatus.done,  # не planned
        done_at=datetime.now(timezone.utc),
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    res = await mark_lesson_done(session, bot, lesson.id)

    assert res is None
    assert bot.sent == []


@pytest.mark.asyncio
async def test_mark_lesson_done_single_raises_if_no_price(session):
    st = Student(
        full_name="A",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,  # замените если у вас другое имя
        price_per_lesson=None,
    )
    session.add(st)
    await session.flush()

    lesson = Lesson(
        student_id=st.id,
        start_at=datetime.now(timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    bot = FakeBot()
    with pytest.raises(ValueError):
        await mark_lesson_done(session, bot, lesson.id)
