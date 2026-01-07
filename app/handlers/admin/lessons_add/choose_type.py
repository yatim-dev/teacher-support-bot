from aiogram import Router, F
from aiogram.types import CallbackQuery

from ....callbacks import AdminCb
from ....keyboards import add_lesson_type_kb
from ..common import get_user, ensure_teacher

router = Router()


@router.callback_query(AdminCb.filter(F.action == "lessons_add"))
async def lesson_add_choose(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    if not callback_data.student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await call.message.edit_text(
        "Добавить занятие:\n\n"
        "• Разовое — создаст один урок на дату/время.\n"
        "• Еженедельное — создаст цикл по дню недели.",
        reply_markup=add_lesson_type_kb(callback_data.student_id)
    )
    await call.answer()
