from aiogram import Router, F
from aiogram.types import CallbackQuery

from sqlalchemy import select, delete, exists, and_

from ...models import User, Student, Lesson, RegistrationKey, Notification, ParentStudent, Parent
from ...callbacks import AdminCb
from ...keyboards import admin_menu, student_delete_confirm_kb
from .common import get_user, ensure_teacher

router = Router()


@router.callback_query(AdminCb.filter(F.action == "student_delete"))
async def student_delete_ask(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    text = (
        "Удаление ученика\n\n"
        f"Ученик: {st.full_name}\n"
        "Будут удалены:\n"
        "• ученик и его пользователь (если зарегистрирован)\n"
        "• все уроки, правила, баланс, начисления\n"
        "• ключи регистрации\n"
        "• уведомления по его урокам\n\n"
        "Действие необратимо. Удалить?"
    )
    await call.message.edit_text(text, reply_markup=student_delete_confirm_kb(student_id))
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "student_delete_confirm"))
async def student_delete_confirm(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    student_user_id = st.user_id

    ps2 = ParentStudent.__table__.alias("ps2")
    parent_ids_to_delete = (await session.execute(
        select(ParentStudent.parent_id)
        .where(ParentStudent.student_id == student_id)
        .where(
            ~exists(
                select(1).select_from(ps2).where(
                    and_(
                        ps2.c.parent_id == ParentStudent.parent_id,
                        ps2.c.student_id != student_id
                    )
                )
            )
        )
        .distinct()
    )).scalars().all()

    parent_user_ids_to_delete: list[int] = []
    if parent_ids_to_delete:
        parent_user_ids_to_delete = (await session.execute(
            select(Parent.user_id).where(Parent.id.in_(parent_ids_to_delete))
        )).scalars().all()

    lesson_ids = (await session.execute(
        select(Lesson.id).where(Lesson.student_id == student_id)
    )).scalars().all()

    if lesson_ids:
        await session.execute(
            delete(Notification).where(
                Notification.type.in_(("lesson_24h", "lesson_1h")),
                Notification.entity_id.in_(lesson_ids),
            )
        )

    await session.execute(delete(RegistrationKey).where(RegistrationKey.student_id == student_id))

    await session.delete(st)

    if student_user_id:
        await session.execute(delete(User).where(User.id == student_user_id))

    if parent_user_ids_to_delete:
        await session.execute(delete(User).where(User.id.in_(parent_user_ids_to_delete)))

    await session.commit()

    await call.message.edit_text("Ученик и связанные данные удалены.", reply_markup=admin_menu())
    await call.answer()
