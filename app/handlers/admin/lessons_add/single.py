from datetime import date, time as dtime

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ....callbacks import AdminCb
from ....keyboards import fsm_nav_kb, after_single_added_kb
from ....models import Student, Lesson, LessonStatus
from ....jobs_notifications import plan_lesson_notifications_job
from ..common import get_user, ensure_teacher, local_to_utc
from .states import AddSingleLessonFSM

router = Router()


@router.callback_query(AdminCb.filter(F.action == "add_single"))
async def add_single_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    if not student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await state.update_data(student_id=student_id)
    await state.set_state(AddSingleLessonFSM.date_)

    await call.message.edit_text(
        "Разовое занятие.\nВведите дату YYYY-MM-DD, например `2026-01-10`:",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )
    await call.answer()


@router.message(AddSingleLessonFSM.date_)
async def add_single_date(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    txt = (message.text or "").strip()
    try:
        y, m, d = map(int, txt.split("-"))
        dval = date(y, m, d)
    except Exception:
        await message.answer(
            "Формат YYYY-MM-DD, например `2026-01-10`",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    await state.update_data(date_=dval)
    await state.set_state(AddSingleLessonFSM.time_)

    await message.answer(
        "Введите время HH:MM (локальное время ученика), например 16:30",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )


@router.message(AddSingleLessonFSM.time_)
async def add_single_time(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    txt = (message.text or "").strip()
    try:
        hh, mm = txt.split(":")
        tval = dtime(hour=int(hh), minute=int(mm))
    except Exception:
        await message.answer(
            "Формат HH:MM, например 16:30",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    await state.update_data(time_=tval)
    await state.set_state(AddSingleLessonFSM.duration)

    await message.answer(
        "Введите длительность (мин), например 60",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )


@router.message(AddSingleLessonFSM.duration)
async def add_single_duration(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    try:
        dur = int((message.text or "").strip())
        if dur <= 0 or dur > 600:
            raise ValueError
    except Exception:
        await message.answer(
            "Введите целое число 1..600",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    dval: date = data["date_"]
    tval: dtime = data["time_"]

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    start_at_utc = local_to_utc(st.timezone, dval, tval)

    lesson = Lesson(
        student_id=student_id,
        start_at=start_at_utc,
        duration_min=dur,
        status=LessonStatus.planned,
        source_rule_id=None
    )
    session.add(lesson)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await message.answer(
            "На это время уже есть занятие.\nНажмите «Назад» и выберите другую дату/время.",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    await plan_lesson_notifications_job()

    await state.clear()
    await message.answer(
        "Разовое занятие создано.\nНапоминания запланированы.\n\nКуда перейти?",
        reply_markup=after_single_added_kb(student_id)
    )
