from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from ..config import settings
from ..models import User
from ..services.auth import ensure_teacher_user, register_by_key
from ..keyboards import tz_kb

router = Router()


class Reg(StatesGroup):
    waiting_key = State()


@router.message(F.text == "/start")
async def start(message: Message, state: FSMContext, session):
    tg_id = message.from_user.id
    full_name = message.from_user.full_name

    # 1) если это учитель — создаём/находим автоматически
    teacher = await ensure_teacher_user(session, tg_id, full_name, settings.teacher_tg_id)
    if teacher:
        await message.answer("Вы вошли как учитель. Напишите /menu")
        return

    # 2) если уже зарегистрирован
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user:
        # попросим TZ, если нет
        if not user.timezone:
            await message.answer("Выберите ваш часовой пояс:", reply_markup=tz_kb())
        await message.answer("Вы уже зарегистрированы. /menu")
        return

    await state.set_state(Reg.waiting_key)
    await message.answer("Введите ключ регистрации:")


@router.message(Reg.waiting_key)
async def process_key(message: Message, state: FSMContext, session):
    ok, text = await register_by_key(
        session=session,
        tg_id=message.from_user.id,
        full_name=message.from_user.full_name,
        key_value=(message.text or "").strip()
    )
    if not ok:
        await message.answer(text)
        return

    await state.clear()
    await message.answer(text)
    await message.answer("Теперь выберите ваш часовой пояс:", reply_markup=tz_kb())
    await message.answer("Дальше: /menu")
