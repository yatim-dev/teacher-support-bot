# app/handlers/help.py
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select

from ..models import User, Role

router = Router()

HELP_COMMON = (
    "Команды:\n"
    "/start — начало\n"
    "/menu — меню\n"
    "/help — помощь\n"
)

HELP_TEACHER = (
    "\nУчитель:\n"
    "- /menu → Админка\n"
    "- создайте ученика\n"
    "- добавьте правило расписания\n"
    "- сгенерируйте ключи ученика/родителя\n"
    "- отмечайте уроки кнопкой «Проведён»\n"
)

HELP_STUDENT = (
    "\nУченик:\n"
    "- /menu → Расписание\n"
    "- будут приходить напоминания об уроках\n"
)

HELP_PARENT = (
    "\nРодитель:\n"
    "- /menu → Дети → выбрать ребёнка → расписание\n"
    "- будут приходить напоминания об уроках\n"
    "- для разовой оплаты: сообщение после «Проведён»\n"
)

@router.message(F.text == "/help")
async def help_cmd(message: Message, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one_or_none()

    if not user:
        await message.answer(HELP_COMMON + "\nСначала зарегистрируйтесь через /start и ключ.")
        return

    text = HELP_COMMON
    if user.role == Role.teacher:
        text += HELP_TEACHER
    elif user.role == Role.student:
        text += HELP_STUDENT
    elif user.role == Role.parent:
        text += HELP_PARENT

    await message.answer(text)
