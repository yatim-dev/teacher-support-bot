import secrets
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery

from sqlalchemy import select

from ...models import User, Role, Student, RegistrationKey
from ...callbacks import AdminCb
from ...keyboards import students_list_kb, student_card_kb
from .common import get_user, ensure_teacher

router = Router()


@router.callback_query(AdminCb.filter(F.action == "students"))
async def admin_students(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    page = callback_data.page
    page_size = 10
    offset = (page - 1) * page_size

    rows = (await session.execute(
        select(Student.id, Student.full_name)
        .order_by(Student.full_name)
        .offset(offset)
        .limit(page_size)
    )).all()

    await call.message.edit_text("Ученики:", reply_markup=students_list_kb(rows, page))
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "student"))
async def admin_student_card(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    st = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()

    txt = (
        f"Ученик: {st.full_name}\n"
        f"TZ ученика: {st.timezone}\n"
        f"Тариф: {st.billing_mode.value}\n"
        f"Цена за урок (если single): {st.price_per_lesson or '-'}\n"
        f"Зарегистрирован: {'да' if st.user_id else 'нет'}\n\n"
        "Дальше:\n"
        "1) Добавить занятие (разовое или еженедельное)\n"
        "2) Сгенерировать ключи (ученик/родитель)\n"
        "3) Проверить ближайшие уроки"
    )
    await call.message.edit_text(txt, reply_markup=student_card_kb(st.id))
    await call.answer()


async def create_key(session, role_target: Role, student_id: int) -> str:
    key = secrets.token_urlsafe(10)
    rk = RegistrationKey(
        key=key,
        role_target=role_target,
        student_id=student_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        max_uses=1,
        used_count=0,
        active=True
    )
    session.add(rk)
    await session.commit()
    return key


@router.callback_query(AdminCb.filter(F.action.in_({"keys_student", "keys_parent"})))
async def admin_keys(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    st = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()

    if callback_data.action == "keys_student":
        key = await create_key(session, Role.student, st.id)
        text = (
            f"Ключ для ученика ({st.full_name}):\n"
            f"`{key}`\n\n"
            f"Инструкция: ученик пишет боту /start и вводит этот ключ."
        )
    else:
        key = await create_key(session, Role.parent, st.id)
        text = (
            f"Ключ для родителя ({st.full_name}):\n"
            f"`{key}`\n\n"
            f"Инструкция: родитель пишет боту /start и вводит этот ключ."
        )

    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=student_card_kb(st.id))
    await call.answer()
