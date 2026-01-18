from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, func

from .admin.common import ensure_teacher, get_user
from ..models import User, Role, Student, Lesson, LessonStatus, BillingMode, StudentBalance, LessonCharge, ChargeStatus
from ..callbacks import MenuCb, AdminCb
from ..utils_time import fmt_dt_for_tz
from ..services.homework import homework_avg_last_n
from ..keyboards import student_schedule_homework_kb, student_card_kb  # <-- убедись, что импорт есть

router = Router()


@router.callback_query(MenuCb.filter(F.section == "student_schedule"))
async def student_schedule(call: CallbackQuery, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    if user.role != Role.student:
        await call.answer("Недоступно", show_alert=True)
        return

    student = (await session.execute(select(Student).where(Student.user_id == user.id))).scalar_one()

    board_line = ""
    if getattr(student, "board_url", None):
        board_line = f"Ваша доска: {student.board_url}\n\n"

    # средняя оценка ДЗ за последние N (по умолчанию 10)
    avg = await homework_avg_last_n(session, student.id, n=10)
    if avg is None:
        avg_line = "Средняя оценка ДЗ (последние 10): нет данных\n\n"
    else:
        avg_line = f"Средняя оценка ДЗ (последние 10): {avg:.2f}/10\n\n"

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)

    lessons = (await session.execute(
        select(Lesson)
        .where(
            Lesson.student_id == student.id,
            Lesson.status == LessonStatus.planned,
            Lesson.start_at >= now,
            Lesson.start_at <= horizon
        )
        .order_by(Lesson.start_at)
    )).scalars().all()

    tzname = user.timezone or student.timezone or "Europe/Moscow"

    if not lessons:
        await call.message.edit_text(board_line + avg_line + "На ближайшие 7 дней уроков нет.")
        await call.answer()
        return

    lines = [f"- {fmt_dt_for_tz(l.start_at, tzname)} ({tzname})" for l in lessons]

    await call.message.edit_text(
        board_line
        + avg_line
        + "Ваши уроки (7 дней):\n"
        + "\n".join(lines)
        + "\n\nНажмите «ДЗ» для просмотра.",
        reply_markup=student_schedule_homework_kb(student.id, lessons),
    )
    await call.answer()


async def render_student_card(message: Message, session, student_id: int) -> None:
    st = (await session.execute(
        select(Student).where(Student.id == student_id)
    )).scalar_one()

    board_line = f"Доска: {st.board_url or '-'}\n"

    left_line = ""
    show_sub_buttons = False
    if st.billing_mode == BillingMode.subscription:
        bal = (await session.execute(
            select(StudentBalance).where(StudentBalance.student_id == st.id)
        )).scalar_one_or_none()
        left = bal.lessons_left if bal else 0
        left_line = f"Осталось уроков: {left}\n"
        show_sub_buttons = True

    unpaid_line = ""
    if st.billing_mode == BillingMode.single:
        unpaid_cnt = (await session.execute(
            select(func.count())
            .select_from(LessonCharge)
            .where(
                LessonCharge.student_id == st.id,
                LessonCharge.status == ChargeStatus.pending
            )
        )).scalar_one()
        unpaid_line = f"Проведено, но не оплачено: {unpaid_cnt}\n"

    txt = (
        f"Ученик: {st.full_name}\n"
        f"TZ ученика: {st.timezone}\n"
        f"{board_line}"
        f"Тариф: {st.billing_mode.value}\n"
        f"{left_line}"
        f"{unpaid_line}"
        f"Цена за урок (если single): {st.price_per_lesson or '-'}\n"
        f"Зарегистрирован: {'да' if st.user_id else 'нет'}\n\n"
        "Дальше:\n"
        "1) Добавить занятие (разовое или еженедельное)\n"
        "2) Сгенерировать ключи (ученик/родитель)\n"
        "3) Проверить ближайшие уроки"
    )

    await message.edit_text(
        txt,
        reply_markup=student_card_kb(st.id, show_subscription=show_sub_buttons)
    )


@router.callback_query(AdminCb.filter(F.action == "student"))
async def admin_student_card(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    await render_student_card(call.message, session, student_id=callback_data.student_id)
    await call.answer()