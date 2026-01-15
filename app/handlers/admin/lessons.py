from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, delete, or_, and_, exists

from ...models import Lesson, LessonStatus, Student, ScheduleRule, LessonCharge, ChargeStatus, BillingMode, User
from ...callbacks import LessonCb, AdminCb
from ...keyboards import lesson_actions_kb, student_card_kb
from ...utils_time import fmt_dt_for_tz
from ...services.billing import mark_lesson_done
from .common import ensure_teacher

router = Router()

async def render_lesson_card(call: CallbackQuery, session, student_id: int, offset: int):
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    # done-уроки показываем только если есть pending (проведён, но не оплачен)
    unpaid_done_exists = exists(
        select(1).where(
            LessonCharge.lesson_id == Lesson.id,
            LessonCharge.status == ChargeStatus.pending
        )
    )

    lessons = (await session.execute(
        select(Lesson)
        .where(
            Lesson.student_id == student_id,
            Lesson.status != LessonStatus.canceled,
            or_(
                Lesson.status == LessonStatus.planned,
                and_(Lesson.status == LessonStatus.done, unpaid_done_exists),
            )
        )
        .order_by(Lesson.start_at)
        .offset(offset)
        .limit(1)
    )).scalars().all()

    if not lessons:
        await call.message.edit_text("Ближайших уроков нет.", reply_markup=student_card_kb(student_id))
        return

    lesson = lessons[0]
    when = fmt_dt_for_tz(lesson.start_at, st.timezone)
    is_recurring = lesson.source_rule_id is not None

    # Оплата (важно для single): может быть отмечена заранее, до проведения
    pay_line = ""
    paid = False
    if st.billing_mode == BillingMode.single:
        ch = (await session.execute(
            select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
        )).scalar_one_or_none()

        if ch and ch.status == ChargeStatus.paid:
            paid = True
            pay_line = "Оплата: оплачено\n"
        else:
            pay_line = "Оплата: не оплачено\n"

    status_text = "planned" if lesson.status == LessonStatus.planned else "done"

    text = (
        f"{st.full_name}\n"
        f"Урок: {when} ({st.timezone})\n"
        f"Тип: {'еженедельное' if is_recurring else 'разовое'}\n"
        f"Статус: {status_text}\n"
        f"{pay_line}"
    ).rstrip()

    await call.message.edit_text(
        text,
        reply_markup=lesson_actions_kb(
            lesson.id, student_id, offset,
            is_recurring=is_recurring,
            show_done=(lesson.status == LessonStatus.planned),
            show_pay=(st.billing_mode == BillingMode.single and not paid),
        )
    )

@router.callback_query(AdminCb.filter(F.action == "lessons"))
async def admin_lessons(call: CallbackQuery, callback_data: AdminCb, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    if not callback_data.student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await render_lesson_card(call, session, callback_data.student_id, offset=0)
    await call.answer()


@router.callback_query(LessonCb.filter())
async def lesson_action(call: CallbackQuery, callback_data: LessonCb, session, bot):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    student_id = callback_data.student_id or 0
    offset = callback_data.offset

    if callback_data.action == "next":
        await render_lesson_card(call, session, student_id, offset=offset + 1)
        await call.answer()
        return

    if callback_data.action == "prev":
        await render_lesson_card(call, session, student_id, offset=max(0, offset - 1))
        await call.answer()
        return

    if callback_data.action == "cancel":
        lesson = (await session.execute(select(Lesson).where(Lesson.id == callback_data.lesson_id))).scalar_one()

        # Разовое: отмена = удалить из календаря
        if lesson.source_rule_id is None:
            await session.delete(lesson)
            await session.commit()
            await call.message.edit_text(
                "Разовое занятие отменено и удалено из календаря.",
                reply_markup=student_card_kb(student_id)
            )
            await call.answer()
            return

        # Еженедельное: отменяем только ближайшее занятие (НЕ удаляем, чтобы генератор не пересоздал)
        lesson.status = LessonStatus.canceled
        await session.commit()
        await call.message.edit_text(
            "Ближайшее занятие из еженедельного цикла отменено.",
            reply_markup=student_card_kb(student_id)
        )
        await call.answer()
        return

    if callback_data.action == "delete_series":
        lesson = (await session.execute(select(Lesson).where(Lesson.id == callback_data.lesson_id))).scalar_one()

        if lesson.source_rule_id is None:
            await call.answer("Это разовое занятие. Цикла нет.", show_alert=True)
            return

        rule_id = lesson.source_rule_id
        now = datetime.now(timezone.utc)

        # ВАЖНО: сначала удаляем будущие уроки, потом удаляем правило.
        # Иначе из-за FK ondelete="SET NULL" уроки потеряют source_rule_id и станут выглядеть как разовые.
        await session.execute(
            delete(Lesson).where(
                Lesson.source_rule_id == rule_id,
                Lesson.start_at >= now
            )
        )

        rule = (await session.execute(select(ScheduleRule).where(ScheduleRule.id == rule_id))).scalar_one()
        await session.delete(rule)

        await session.commit()
        await call.message.edit_text(
            "Еженедельный цикл удалён (правило и будущие занятия).",
            reply_markup=student_card_kb(student_id)
        )
        await call.answer()
        return

    if callback_data.action == "done":
        await mark_lesson_done(session, bot, callback_data.lesson_id)

        # перерисовываем карточку урока на том же offset
        # (если single — появится "Урок оплачен", если subscription — урок исчезнет)
        await render_lesson_card(call, session, student_id=student_id, offset=offset)

        await call.answer()
        return
