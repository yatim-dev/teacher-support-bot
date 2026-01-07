from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from ....callbacks import FsmNavCb
from ....keyboards import fsm_nav_kb, add_lesson_type_kb, student_card_kb
from ..common import get_user, ensure_teacher
from .states import AddRuleFSM, AddSingleLessonFSM

router = Router()


@router.callback_query(FsmNavCb.filter(F.flow == "add_rule"))
async def fsm_add_rule_nav(call: CallbackQuery, callback_data: FsmNavCb, state: FSMContext):
    data = await state.get_data()
    student_id = callback_data.student_id or data.get("student_id")

    if callback_data.action == "cancel":
        await state.clear()
        if student_id:
            await call.message.edit_text("Отменено.", reply_markup=student_card_kb(student_id))
        else:
            await call.message.edit_text("Отменено.")
        await call.answer()
        return

    current = await state.get_state()

    if current == AddRuleFSM.start_date.state:
        await state.set_state(AddRuleFSM.duration)
        await call.message.edit_text("Введите длительность (мин), например 60", reply_markup=fsm_nav_kb("add_rule", student_id))
    elif current == AddRuleFSM.duration.state:
        await state.set_state(AddRuleFSM.time_local)
        await call.message.edit_text("Введите время HH:MM (локальное время ученика), например 16:30", reply_markup=fsm_nav_kb("add_rule", student_id))
    elif current == AddRuleFSM.time_local.state:
        await state.set_state(AddRuleFSM.weekday)
        await call.message.edit_text(
            "Введите день недели:\n1=ПН\n2=ВТ\n3=СР\n4=ЧТ\n5=ПТ\n6=СБ\n7=ВС",
            reply_markup=fsm_nav_kb("add_rule", student_id)
        )
    else:
        await state.clear()
        await call.message.edit_text("Добавить занятие:", reply_markup=add_lesson_type_kb(student_id))

    await call.answer()


@router.callback_query(FsmNavCb.filter(F.flow == "add_single"))
async def fsm_add_single_nav(call: CallbackQuery, callback_data: FsmNavCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = callback_data.student_id or data.get("student_id")

    if callback_data.action == "cancel":
        await state.clear()
        await call.message.edit_text("Отменено.", reply_markup=student_card_kb(student_id))
        await call.answer()
        return

    current = await state.get_state()

    if current == AddSingleLessonFSM.duration.state:
        await state.set_state(AddSingleLessonFSM.time_)
        await call.message.edit_text(
            "Введите время HH:MM (локальное время ученика), например 16:30",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
    elif current == AddSingleLessonFSM.time_.state:
        await state.set_state(AddSingleLessonFSM.date_)
        await call.message.edit_text(
            "Введите дату YYYY-MM-DD, например 2026-01-10:",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
    else:
        await state.clear()
        await call.message.edit_text("Добавить занятие:", reply_markup=add_lesson_type_kb(student_id))

    await call.answer()
