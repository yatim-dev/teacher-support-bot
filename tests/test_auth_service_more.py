from datetime import datetime, timezone, timedelta

import pytest

from app.models import RegistrationKey, Role, Student
from app.services.auth import register_by_key


@pytest.mark.asyncio
async def test_register_by_key_inactive_key(session):
    key = RegistrationKey(
        key="INACTIVE",
        active=False,
        expires_at=None,
        used_count=0,
        max_uses=10,
        role_target=Role.student,
        student_id=None,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=1, full_name="X", key_value="INACTIVE")
    assert ok is False
    assert "недейств" in msg.lower()


@pytest.mark.asyncio
async def test_register_by_key_expired_key(session):
    key = RegistrationKey(
        key="EXPIRED",
        active=True,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        used_count=0,
        max_uses=10,
        role_target=Role.student,
        student_id=None,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=1, full_name="X", key_value="EXPIRED")
    assert ok is False
    assert "недейств" in msg.lower()


@pytest.mark.asyncio
async def test_register_by_key_max_uses_reached(session):
    key = RegistrationKey(
        key="USEDUP",
        active=True,
        expires_at=None,
        used_count=5,
        max_uses=5,
        role_target=Role.student,
        student_id=None,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=1, full_name="X", key_value="USEDUP")
    assert ok is False
    assert "недейств" in msg.lower()


@pytest.mark.asyncio
async def test_register_by_key_student_key_without_student_id_returns_error(session):
    key = RegistrationKey(
        key="ST_NO_STUDENT",
        active=True,
        expires_at=None,
        used_count=0,
        max_uses=10,
        role_target=Role.student,
        student_id=None,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=1, full_name="X", key_value="ST_NO_STUDENT")
    assert ok is False
    assert "ключ студента" in msg.lower()


@pytest.mark.asyncio
async def test_register_by_key_parent_key_without_student_id_returns_error(session):
    key = RegistrationKey(
        key="PR_NO_STUDENT",
        active=True,
        expires_at=None,
        used_count=0,
        max_uses=10,
        role_target=Role.parent,
        student_id=None,
    )
    session.add(key)
    await session.commit()

    ok, msg = await register_by_key(session, tg_id=1, full_name="X", key_value="PR_NO_STUDENT")
    assert ok is False
    assert "ключ родителя" in msg.lower()
