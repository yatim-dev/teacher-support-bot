from __future__ import annotations

import pytest
from datetime import date, time, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import (
    User, Role,
    Student, BillingMode,
    Parent, ParentStudent,
    Lesson, LessonStatus,
)
from app.callbacks import AdminCb


# ---- fakes ----
class FakeFromUser:
    def __init__(self, user_id: int, full_name: str = "X"):
        self.id = user_id
        self.full_name = full_name


class FakeMessage:
    def __init__(self, from_user: FakeFromUser):
        self.from_user = from_user
        self.edits = []

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))


class FakeCallbackQuery:
    def __init__(self, from_user: FakeFromUser, message: FakeMessage):
        self.from_user = from_user
        self.message = message
        self.answered = 0

    async def answer(self, *args, **kwargs):
        self.answered += 1


@pytest.mark.asyncio
async def test_common_ensure_teacher_raises_for_non_teacher():
    from app.handlers.admin.common import ensure_teacher

    u = User(tg_id=1, role=Role.parent, name="P", timezone="Europe/Moscow")
    with pytest.raises(PermissionError):
        ensure_teacher(u)

    with pytest.raises(PermissionError):
        ensure_teacher(None)


def test_common_local_to_utc_exact_conversion_moscow():
    """
    Europe/Moscow = UTC+3 без DST -> можно точно проверить.
    2026-01-01 10:00 MSK = 2026-01-01 07:00 UTC
    """
    from app.handlers.admin.common import local_to_utc

    dt_utc = local_to_utc("Europe/Moscow", date(2026, 1, 1), time(10, 0))
    assert dt_utc == datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_student_delete_confirm_does_not_delete_parent_user_if_parent_has_another_child(session):
    """
    Проверяем важную ветку в student_delete_confirm:
    если родитель привязан к удаляемому ученику И к другому ученику,
    то родителя (и его user) удалять нельзя.
    """
    import app.handlers.admin.student_delete as del_mod

    # teacher
    teacher = User(tg_id=8000, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(teacher)
    await session.flush()

    # parent user + parent
    parent_user = User(tg_id=8001, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(parent_user)
    await session.flush()

    parent = Parent(user_id=parent_user.id, full_name="Parent")
    session.add(parent)
    await session.flush()

    # two students
    st1 = Student(full_name="S1", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    st2 = Student(full_name="S2", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add_all([st1, st2])
    await session.flush()

    # parent linked to both students
    session.add_all([
        ParentStudent(parent_id=parent.id, student_id=st1.id),
        ParentStudent(parent_id=parent.id, student_id=st2.id),
    ])

    # add a lesson for st1 (чтобы deletion проходила по “обычному” пути)
    lesson = Lesson(
        student_id=st1.id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = AdminCb(action="student_delete_confirm", student_id=st1.id)
    await del_mod.student_delete_confirm(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    assert "удалены" in msg.edits[0][0].lower()

    # st1 удалён
    st1_db = (await session.execute(select(Student).where(Student.id == st1.id))).scalar_one_or_none()
    assert st1_db is None

    # st2 остался
    st2_db = (await session.execute(select(Student).where(Student.id == st2.id))).scalar_one_or_none()
    assert st2_db is not None

    # parent user НЕ удалён (важно!)
    pu_db = (await session.execute(select(User).where(User.id == parent_user.id))).scalar_one_or_none()
    assert pu_db is not None
