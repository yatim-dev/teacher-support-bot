from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from app.models import User, Role, RegistrationKey, Student, Parent, ParentStudent
from app.services.auth import ensure_teacher_user, register_by_key


@pytest.mark.asyncio
async def test_ensure_teacher_user_returns_none_for_non_teacher(session):
    u = await ensure_teacher_user(session, tg_id=1, full_name="X", teacher_tg_id=999)
    assert u is None

    # пользователь не создан
    found = (await session.execute(select(User).where(User.tg_id == 1))).scalar_one_or_none()
    assert found is None


@pytest.mark.asyncio
async def test_ensure_teacher_user_creates_teacher(session):
    u = await ensure_teacher_user(session, tg_id=999, full_name="Teacher", teacher_tg_id=999)
    assert u is not None
    assert u.tg_id == 999
    assert u.role == Role.teacher


@pytest.mark.asyncio
async def test_register_by_key_invalid_key(session):
    ok, msg = await register_by_key(session, tg_id=10, full_name="A", key_value="nope")
    assert ok is False
    assert "недейств" in msg.lower()


@pytest.mark.asyncio
async def test_register_by_key_student_links_student_user_id_and_deactivates_on_max_uses(session):
    st = Student(full_name="Student", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    key = RegistrationKey(
        key="K1",
        active=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        used_count=0,
        max_uses=1,
        role_target=Role.student,
        student_id=st.id,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=101, full_name="Stud User", key_value="K1")
    assert ok is True

    # student получил user_id
    st2 = (await session.execute(select(Student).where(Student.id == st.id))).scalar_one()
    assert st2.user_id is not None

    # ключ деактивировался
    key2 = (await session.execute(select(RegistrationKey).where(RegistrationKey.key == "K1"))).scalar_one()
    assert key2.used_count == 1
    assert key2.active is False


@pytest.mark.asyncio
async def test_register_by_key_parent_creates_parent_and_link(session):
    st = Student(full_name="Student", timezone="Europe/Moscow")
    session.add(st)
    await session.flush()

    key = RegistrationKey(
        key="K2",
        active=True,
        expires_at=None,
        used_count=0,
        max_uses=5,
        role_target=Role.parent,
        student_id=st.id,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=202, full_name="Parent Name", key_value="K2")
    assert ok is True

    # пользователь-родитель создан
    pu = (await session.execute(select(User).where(User.tg_id == 202))).scalar_one()
    assert pu.role == Role.parent

    # Parent создан и связан с User
    parent = (await session.execute(select(Parent).where(Parent.user_id == pu.id))).scalar_one()
    assert parent.full_name == "Parent Name"

    # ParentStudent создан
    link = (await session.execute(
        select(ParentStudent).where(ParentStudent.parent_id == parent.id, ParentStudent.student_id == st.id)
    )).scalar_one_or_none()
    assert link is not None
