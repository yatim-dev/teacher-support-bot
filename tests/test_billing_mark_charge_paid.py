from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Student, BillingMode, Lesson, LessonStatus, LessonCharge, ChargeStatus
from app.services.billing import mark_charge_paid


@pytest.mark.asyncio
async def test_mark_charge_paid_changes_pending_to_paid(session):
    st = Student(full_name="A", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()

    lesson = Lesson(student_id=st.id, start_at=datetime.now(timezone.utc), duration_min=60, status=LessonStatus.done)
    session.add(lesson)
    await session.flush()

    ch = LessonCharge(lesson_id=lesson.id, student_id=st.id, amount=1000.0, status=ChargeStatus.pending)
    session.add(ch)
    await session.commit()

    await mark_charge_paid(session, ch.id)

    ch2 = (await session.execute(select(LessonCharge).where(LessonCharge.id == ch.id))).scalar_one()
    assert ch2.status == ChargeStatus.paid
    assert ch2.paid_at is not None


@pytest.mark.asyncio
async def test_mark_charge_paid_does_nothing_if_already_paid(session):
    st = Student(full_name="A", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()

    lesson = Lesson(student_id=st.id, start_at=datetime.now(timezone.utc), duration_min=60, status=LessonStatus.done)
    session.add(lesson)
    await session.flush()

    paid_at = datetime.now(timezone.utc)
    ch = LessonCharge(
        lesson_id=lesson.id,
        student_id=st.id,
        amount=1000.0,
        status=ChargeStatus.paid,
        paid_at=paid_at
    )
    session.add(ch)
    await session.commit()

    await mark_charge_paid(session, ch.id)

    ch2 = (await session.execute(select(LessonCharge).where(LessonCharge.id == ch.id))).scalar_one()
    assert ch2.status == ChargeStatus.paid
    assert ch2.paid_at == paid_at
