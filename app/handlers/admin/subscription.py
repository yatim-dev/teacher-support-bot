from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select

from ...callbacks import SubCb
from ...models import User
from .common import get_user, ensure_teacher
from ...services.billing import add_subscription_package

router = Router()

@router.callback_query(SubCb.filter(F.action == "add"))
async def sub_add(call: CallbackQuery, callback_data: SubCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    try:
        left = await add_subscription_package(session, callback_data.student_id, callback_data.qty)
    except ValueError as e:
        await call.answer(str(e), show_alert=True)
        return

    await call.answer(f"Добавлено {callback_data.qty}. Осталось: {left} уроков", show_alert=True)
