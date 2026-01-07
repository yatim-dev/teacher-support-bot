from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from ...callbacks import BoardCb
from ...models import Student
from .common import get_user, ensure_teacher
from ...keyboards import student_card_kb

router = Router()

class EditBoardFSM(StatesGroup):
    url = State()

@router.callback_query(BoardCb.filter(F.action == "edit"))
async def board_edit_start(call: CallbackQuery, callback_data: BoardCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    st = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()
    await state.update_data(student_id=st.id)
    await state.set_state(EditBoardFSM.url)

    await call.message.edit_text(
        f"Текущая доска:\n{st.board_url or '-'}\n\n"
        "Отправьте новую ссылку (https://...) или '-' чтобы очистить."
    )
    await call.answer()

@router.message(EditBoardFSM.url)
async def board_edit_set(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data["student_id"]

    txt = (message.text or "").strip()
    if txt in ("", "-", "—"):
        url = None
    else:
        if not (txt.startswith("http://") or txt.startswith("https://")):
            await message.answer("Ссылка должна начинаться с http:// или https://. Повторите или отправьте '-'.")
            return
        url = txt

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    st.board_url = url
    await session.commit()
    await state.clear()

    await message.answer("Ссылка сохранена.", reply_markup=student_card_kb(student_id))
