from datetime import date, time as dtime

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from ..common import get_user, ensure_teacher
from ....callbacks import AdminCb
from ....keyboards import fsm_nav_kb, after_rule_added_kb
from ....models import ScheduleRule
from ....services.schedule import generate_lessons_for_student
from ....jobs_notifications import plan_lesson_notifications_job
from .states import AddRuleFSM

router = Router()


@router.callback_query(AdminCb.filter(F.action == "add_rule"))
async def add_rule_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    await state.update_data(student_id=callback_data.student_id)
    await state.set_state(AddRuleFSM.weekday)

    await call.message.edit_text(
        "Еженедельное занятие.\n\n"
        "Введите день недели:\n"
        "1 = ПН\n2 = ВТ\n3 = СР\n4 = ЧТ\n5 = ПТ\n6 = СБ\n7 = ВС",
        reply_markup=fsm_nav_kb("add_rule", callback_data.student_id)
    )
    await call.answer()


@router.message(AddRuleFSM.weekday)
async def add_rule_weekday(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    try:
        wd_user = int((message.text or "").strip())
        if wd_user < 1 or wd_user > 7:
            raise ValueError
    except Exception:
        await message.answer(
            "Введите число 1..7: 1=ПН, 2=ВТ, 3=СР, 4=ЧТ, 5=ПТ, 6=СБ, 7=ВС",
            reply_markup=fsm_nav_kb("add_rule", student_id)
        )
        return

    await state.update_data(weekday=wd_user - 1)
    await state.set_state(AddRuleFSM.time_local)

    await message.answer(
        "Введите время HH:MM (локальное время ученика), например 16:30",
        reply_markup=fsm_nav_kb("add_rule", student_id)
    )


@router.message(AddRuleFSM.time_local)
async def add_rule_time(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    txt = (message.text or "").strip()
    try:
        hh, mm = txt.split(":")
        t = dtime(hour=int(hh), minute=int(mm))
    except Exception:
        await message.answer("Формат HH:MM, например 16:30")
        return

    await state.update_data(time_local=t)
    await state.set_state(AddRuleFSM.duration)
    await message.answer("Введите длительность (мин), например 60")


@router.message(AddRuleFSM.duration)
async def add_rule_duration(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    try:
        dur = int((message.text or "").strip())
        if dur <= 0 or dur > 600:
            raise ValueError
    except Exception:
        await message.answer("Введите целое число 1..600")
        return

    await state.update_data(duration_min=dur)
    await state.set_state(AddRuleFSM.start_date)
    await message.answer("Введите дату начала YYYY-MM-DD, например 2026-01-10")


@router.message(AddRuleFSM.start_date)
async def add_rule_start_date(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    txt = (message.text or "").strip()
    try:
        y, m, d = map(int, txt.split("-"))
        sd = date(y, m, d)
    except Exception:
        await message.answer("Формат YYYY-MM-DD, например 2026-01-10")
        return

    data = await state.get_data()
    student_id = data["student_id"]

    rule = ScheduleRule(
        student_id=student_id,
        weekday=data["weekday"],
        time_local=data["time_local"],
        duration_min=data["duration_min"],
        start_date=sd,
        end_date=None,
        active=True
    )
    session.add(rule)
    await session.commit()

    _ = await generate_lessons_for_student(session, student_id)
    await session.commit()

    await plan_lesson_notifications_job()

    await state.clear()
    await message.answer(
        "Еженедельное правило добавлено.\nУроки сгенерированы и напоминания запланированы.\n\nКуда перейти?",
        reply_markup=after_rule_added_kb(student_id)
    )
