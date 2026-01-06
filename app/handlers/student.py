from datetime import datetime, timedelta, timezone
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select

from ..models import User, Role, Student, Lesson, LessonStatus
from ..callbacks import MenuCb
from ..utils_time import fmt_dt_for_tz
from ..services.homework import homework_avg_last_n

router = Router()


@router.callback_query(MenuCb.filter(F.section == "student_schedule"))
async def student_schedule(call: CallbackQuery, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    if user.role != Role.student:
        await call.answer("Недоступно", show_alert=True)
        return

    student = (await session.execute(select(Student).where(Student.user_id == user.id))).scalar_one()

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

    if not lessons:
        await call.message.edit_text(avg_line + "На ближайшие 7 дней уроков нет.")
        await call.answer()
        return

    lines = []
    tzname = user.timezone or "Europe/Moscow"
    for l in lessons:
        lines.append(f"- {fmt_dt_for_tz(l.start_at, tzname)} ({tzname})")

    await call.message.edit_text(avg_line + "Ваши уроки (7 дней):\n" + "\n".join(lines))
    await call.answer()