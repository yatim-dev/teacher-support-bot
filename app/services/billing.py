from datetime import datetime, timezone
from sqlalchemy import select, update

from ..models import (
    Lesson, LessonStatus, Student, BillingMode,
    StudentBalance, LessonCharge, ChargeStatus,
    ParentStudent, Parent, User
)
from ..utils_time import fmt_dt_for_tz


async def mark_lesson_done(session, bot, lesson_id: int) -> int | None:
    lesson = (await session.execute(select(Lesson).where(Lesson.id == lesson_id))).scalar_one()

    if lesson.status != LessonStatus.planned:
        return None

    student = (await session.execute(select(Student).where(Student.id == lesson.student_id))).scalar_one()

    lesson.status = LessonStatus.done
    lesson.done_at = datetime.now(timezone.utc)

    # subscription -> списание
    if student.billing_mode == BillingMode.subscription:
        bal = (await session.execute(
            select(StudentBalance).where(StudentBalance.student_id == student.id)
        )).scalar_one_or_none()

        if not bal:
            bal = StudentBalance(student_id=student.id, lessons_left=0)
            session.add(bal)
            await session.flush()

        if bal.lessons_left > 0:
            bal.lessons_left -= 1

        await session.commit()
        return None

    # single -> начисление + уведомление родителям
    if not student.price_per_lesson:
        raise ValueError("Для single нужен price_per_lesson у ученика")

    charge = LessonCharge(
        lesson_id=lesson.id,
        student_id=student.id,
        amount=float(student.price_per_lesson),
        status=ChargeStatus.pending
    )
    session.add(charge)
    await session.flush()

    # всем родителям
    parent_ids = (await session.execute(
        select(ParentStudent.parent_id).where(ParentStudent.student_id == student.id)
    )).scalars().all()

    if parent_ids:
        parent_user_ids = (await session.execute(
            select(Parent.user_id).where(Parent.id.in_(parent_ids))
        )).scalars().all()

        parent_users = (await session.execute(
            select(User).where(User.id.in_(parent_user_ids))
        )).scalars().all()

        for pu in parent_users:
            when = fmt_dt_for_tz(lesson.start_at, pu.timezone)
            tzname = pu.timezone or "Europe/Moscow"
            text = (
                f"Урок проведён.\n"
                f"Ученик: {student.full_name}\n"
                f"Дата/время: {when} ({tzname})\n"
                f"К оплате: {charge.amount}"
            )
            await bot.send_message(pu.tg_id, text)

    await session.commit()
    return charge.id


async def mark_charge_paid(session, charge_id: int):
    now = datetime.now(timezone.utc)
    await session.execute(
        update(LessonCharge)
        .where(LessonCharge.id == charge_id, LessonCharge.status == ChargeStatus.pending)
        .values(status=ChargeStatus.paid, paid_at=now)
    )
    await session.commit()
