from aiogram import Router, F
from aiogram.types import CallbackQuery

from ...callbacks import MenuCb
from ...keyboards import admin_menu
from .common import get_user, ensure_teacher

router = Router()


@router.callback_query(MenuCb.filter(F.section == "admin"))
async def admin_root(call: CallbackQuery, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    text = (
        "Админка\n\n"
        "• Ученики — список учеников и управление конкретным учеником.\n"
        "• Создать ученика — добавьте нового ученика (ФИО, TZ, тариф).\n\n"
        "Подсказка: у ученика можно добавить разовое занятие или еженедельный цикл."
    )
    await call.message.edit_text(text, reply_markup=admin_menu())
    await call.answer()
