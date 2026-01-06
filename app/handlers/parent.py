from datetime import datetime, timedelta, timezone
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select

from ..models import User, Role, Parent, ParentStudent, Student, Lesson, LessonStatus
from ..callbacks import MenuCb, ChildCb
from ..keyboards import parent_children_kb
from ..utils_time import fmt_dt_for_tz

router = Router()


@router.callback_query(MenuCb.filter(F.section == "parent_children"))
async def parent_children(call: CallbackQuery, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    if user.role != Role.parent:
        await call.answer("Недоступно", show_alert=True)
        return

    parent = (await session.execute(select(Parent).where(Parent.user_id == user.id))).scalar_one()

    student_ids = (await session.execute(
        select(ParentStudent.student_id).where(ParentStudent.parent_id == parent.id)
    )).scalars().all()

    if not student_ids:
        await call.message.edit_text("К вам не привязан ни один ученик.")
        await call.answer()
        return

    children = (await session.execute(
        select(Student.id, Student.full_name).where(Student.id.in_(student_ids)).order_by(Student.full_name)
    )).all()

    await call.message.edit_text("Выберите ребёнка:", reply_markup=parent_children_kb(children))
    await call.answer()


@router.callback_query(ChildCb.filter())
async def parent_child_schedule(call: CallbackQuery, callback_data: ChildCb, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    if user.role != Role.parent:
        await call.answer("Недоступно", show_alert=True)
        return

    student = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)

    lessons = (await session.execute(
        select(Lesson)
        .where(Lesson.student_id == student.id, Lesson.status == LessonStatus.planned,
               Lesson.start_at >= now, Lesson.start_at <= horizon)
        .order_by(Lesson.start_at)
    )).scalars().all()

    if not lessons:
        await call.message.edit_text(f"{student.full_name}\nНа ближайшие 7 дней уроков нет.")
        await call.answer()
        return

    lines = []
    for l in lessons:
        lines.append(f"- {fmt_dt_for_tz(l.start_at, user.timezone)} ({user.timezone})")

    await call.message.edit_text(f"{student.full_name}\nУроки (7 дней):\n" + "\n".join(lines))
    await call.answer()
