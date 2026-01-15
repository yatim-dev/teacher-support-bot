from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select

from ...models import Lesson, Student, LessonCharge, ChargeStatus, BillingMode
from ...callbacks import LessonPayCb
from .common import get_user, ensure_teacher
from .lessons import render_lesson_card  # ok

router = Router()

@router.callback_query(LessonPayCb.filter(F.action == "paid"))
async def lesson_pay_action(call: CallbackQuery, callback_data: LessonPayCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    lesson = (await session.execute(
        select(Lesson).where(Lesson.id == callback_data.lesson_id)
    )).scalar_one()

    st = (await session.execute(
        select(Student).where(Student.id == lesson.student_id)
    )).scalar_one()

    # абонемент — платить нечего
    if st.billing_mode != BillingMode.single:
        await call.answer("Для абонемента оплата не требуется", show_alert=True)
        return

    ch = (await session.execute(
        select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
    )).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if ch is None:
        ch = LessonCharge(
            lesson_id=lesson.id,
            student_id=st.id,
            amount=float(st.price_per_lesson or 0),
            status=ChargeStatus.paid,
            paid_at=now,
        )
        session.add(ch)
    else:
        ch.status = ChargeStatus.paid
        ch.paid_at = now

    await session.commit()

    # перерисовать карточку
    await render_lesson_card(call, session, student_id=callback_data.student_id, offset=callback_data.offset)
    await call.answer()
