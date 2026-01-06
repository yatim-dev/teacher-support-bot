from datetime import datetime, timezone
from sqlalchemy import select, update

from ..models import User, Role, RegistrationKey, Student, Parent, ParentStudent


async def ensure_teacher_user(session, tg_id: int, full_name: str, teacher_tg_id: int) -> User | None:
    if tg_id != teacher_tg_id:
        return None

    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user:
        return user

    user = User(tg_id=tg_id, role=Role.teacher, name=full_name, timezone=None)
    session.add(user)
    await session.commit()
    return user


async def register_by_key(session, tg_id: int, full_name: str, key_value: str) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)

    reg_key = (await session.execute(
        select(RegistrationKey).where(RegistrationKey.key == key_value)
    )).scalar_one_or_none()

    if (not reg_key or not reg_key.active or
        (reg_key.expires_at and reg_key.expires_at <= now) or
            reg_key.used_count >= reg_key.max_uses):
        return False, "Ключ недействителен."

    role = reg_key.role_target
    user = User(tg_id=tg_id, role=role, name=full_name, timezone=None)
    session.add(user)
    await session.flush()

    if role == Role.student:
        if not reg_key.student_id:
            return False, "Ошибка: ключ студента не привязан к ученику."
        await session.execute(
            update(Student).where(Student.id == reg_key.student_id).values(user_id=user.id)
        )

    if role == Role.parent:
        if not reg_key.student_id:
            return False, "Ошибка: ключ родителя не привязан к ученику."
        parent = Parent(user_id=user.id, full_name=full_name)
        session.add(parent)
        await session.flush()
        session.add(ParentStudent(parent_id=parent.id, student_id=reg_key.student_id))

    reg_key.used_count += 1
    if reg_key.used_count >= reg_key.max_uses:
        reg_key.active = False

    await session.commit()
    return True, "Регистрация завершена."
