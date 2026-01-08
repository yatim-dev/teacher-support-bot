from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

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

    # single -> начисление (если ещё не было) + уведомление родителям
    if not student.price_per_lesson:
        raise ValueError("Для single нужен price_per_lesson у ученика")

    # если оплатили заранее (или уже есть pending) — не создаём новый charge
    existing = (await session.execute(
        select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
    )).scalar_one_or_none()

    if existing is None:
        charge = LessonCharge(
            lesson_id=lesson.id,
            student_id=student.id,
            amount=float(student.price_per_lesson),
            status=ChargeStatus.pending
        )
        session.add(charge)
        await session.flush()
    else:
        charge = existing  # pending или paid

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

            if charge.status == ChargeStatus.paid:
                text = (
                    f"Урок проведён.\n"
                    f"Ученик: {student.full_name}\n"
                    f"Дата/время: {when} ({tzname})\n"
                    f"Оплата: отмечена"
                )
            else:
                text = (
                    f"Урок проведён.\n"
                    f"Ученик: {student.full_name}\n"
                    f"Дата/время: {when} ({tzname})\n"
                    f"К оплате: {charge.amount}"
                )

            await bot.send_message(pu.tg_id, text)

    await session.commit()

    # Возвращаем charge.id только если он pending (может пригодиться, но в новой схеме не обязательно)
    return charge.id if charge.status == ChargeStatus.pending else None


async def mark_charge_paid(session, charge_id: int):
    now = datetime.now(timezone.utc)
    await session.execute(
        update(LessonCharge)
        .where(LessonCharge.id == charge_id, LessonCharge.status == ChargeStatus.pending)
        .values(status=ChargeStatus.paid, paid_at=now)
    )
    await session.commit()


async def add_subscription_package(session, student_id: int, lessons: int) -> int:
    if lessons not in (8, 12):
        raise ValueError("Пакет может быть только 8 или 12 уроков")

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    if st.billing_mode != BillingMode.subscription:
        raise ValueError("Пополнение пакетом доступно только для subscription")

    stmt = insert(StudentBalance).values(student_id=student_id, lessons_left=lessons)
    stmt = stmt.on_conflict_do_update(
        index_elements=[StudentBalance.student_id],   # PK
        set_={"lessons_left": StudentBalance.lessons_left + lessons},
    )
    await session.execute(stmt)
    await session.commit()

    bal = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == student_id)
    )).scalar_one()
    return bal.lessons_left

async def mark_lesson_paid_anytime(session, lesson_id: int) -> int:
    lesson = (await session.execute(select(Lesson).where(Lesson.id == lesson_id))).scalar_one()
    student = (await session.execute(select(Student).where(Student.id == lesson.student_id))).scalar_one()

    if student.billing_mode != BillingMode.single:
        raise ValueError("Отмечать оплату вручную можно только для тарифа single")

    if not student.price_per_lesson:
        raise ValueError("Для single нужен price_per_lesson у ученика")

    now = datetime.now(timezone.utc)

    # 1) гарантируем, что charge существует
    ins = insert(LessonCharge).values(
        lesson_id=lesson.id,
        student_id=student.id,
        amount=float(student.price_per_lesson),
        status=ChargeStatus.paid,
        paid_at=now,
    )

    # если charge уже есть — просто ставим paid
    stmt = ins.on_conflict_do_update(
        index_elements=[LessonCharge.lesson_id],  # важно: должен быть unique/pk по lesson_id
        set_={"status": ChargeStatus.paid, "paid_at": now},
    )

    await session.execute(stmt)
    await session.commit()

    ch = (await session.execute(select(LessonCharge).where(LessonCharge.lesson_id == lesson.id))).scalar_one()
    return ch.id