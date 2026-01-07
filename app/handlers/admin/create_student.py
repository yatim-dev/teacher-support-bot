from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

from ...models import Student, BillingMode
from ...callbacks import AdminCb
from ...keyboards import TZ_LIST
from .common import get_user, ensure_teacher

router = Router()


class CreateStudentFSM(StatesGroup):
    full_name = State()
    tz = State()
    board_url = State()
    billing = State()
    price = State()


@router.callback_query(AdminCb.filter(F.action == "create_student"))
async def create_student_start(call: CallbackQuery, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    await state.set_state(CreateStudentFSM.full_name)
    await call.message.edit_text("Создание ученика.\n\nВведите ФИО ученика (сообщением):")
    await call.answer()


@router.message(CreateStudentFSM.full_name)
async def create_student_full_name(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    full_name = (message.text or "").strip()
    if len(full_name) < 2:
        await message.answer("ФИО слишком короткое. Повторите.")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(CreateStudentFSM.tz)
    await message.answer("Выберите TZ ученика (сообщением, строго из списка):\n" + "\n".join(TZ_LIST))


@router.message(CreateStudentFSM.tz)
async def create_student_tz(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    tz = (message.text or "").strip()
    if tz not in TZ_LIST:
        await message.answer("TZ не из списка. Повторите:\n" + "\n".join(TZ_LIST))
        return

    await state.update_data(tz=tz)

    # ВАЖНО: дальше идём на ввод ссылки, а не на тариф
    await state.set_state(CreateStudentFSM.board_url)
    await message.answer(
        "Введите ссылку на доску (Miro/Google/...)\n"
        "Формат: https://...\n"
        "Если не нужна — отправьте '-'"
    )
    return



@router.message(CreateStudentFSM.billing)
async def create_student_billing(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    val = (message.text or "").strip()
    if val not in ("subscription", "single"):
        await message.answer("Нужно написать: subscription или single.")
        return

    await state.update_data(billing=val)

    if val == "single":
        await state.set_state(CreateStudentFSM.price)
        await message.answer("Введите цену за урок (например 1500):")
    else:
        await finalize_student(message, state, session)


@router.message(CreateStudentFSM.price)
async def create_student_price(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    try:
        price = float((message.text or "").replace(",", "."))
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("Введите число > 0, например 1500")
        return

    await state.update_data(price=price)
    await finalize_student(message, state, session)


async def finalize_student(message: Message, state: FSMContext, session):
    data = await state.get_data()

    billing_mode = BillingMode(data["billing"])
    price = data.get("price")

    st = Student(
        full_name=data["full_name"],
        timezone=data["tz"],
        board_url=data.get("board_url"),
        billing_mode=billing_mode,
        price_per_lesson=price if billing_mode == BillingMode.single else None
    )
    session.add(st)
    await session.commit()
    await state.clear()

    await message.answer(
        f"Ученик создан: {st.full_name}\n"
        f"ID: {st.id}\n\n"
        f"Дальше: /menu → Админка → Ученики → выберите ученика."
    )

@router.message(CreateStudentFSM.board_url)
async def create_student_board_url(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    txt = (message.text or "").strip()
    if txt in ("", "-", "—"):
        await state.update_data(board_url=None)
    else:
        if not (txt.startswith("http://") or txt.startswith("https://")):
            await message.answer("Ссылка должна начинаться с http:// или https://. Повторите или отправьте '-'.")
            return
        await state.update_data(board_url=txt)

    await state.set_state(CreateStudentFSM.billing)
    await message.answer("Выберите тариф: subscription или single (сообщением).")