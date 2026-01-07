from __future__ import annotations

import pytest
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    User, Role,
    Student, BillingMode,
    Lesson, LessonStatus,
    Parent, ParentStudent,
    RegistrationKey,
    Notification, NotificationStatus,
)
from app.callbacks import AdminCb, MenuCb


# ----------------------------
# Fakes for aiogram objects
# ----------------------------
class FakeFromUser:
    def __init__(self, user_id: int, full_name: str = "X"):
        self.id = user_id
        self.full_name = full_name


class FakeMessage:
    def __init__(self, from_user: FakeFromUser, text: str | None = None):
        self.from_user = from_user
        self.text = text
        self.answers: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))


class FakeCallbackQuery:
    def __init__(self, from_user: FakeFromUser, message: FakeMessage):
        self.from_user = from_user
        self.message = message
        self.answered = 0

    async def answer(self):
        self.answered += 1


class FakeFSMContext:
    def __init__(self):
        self.state = None
        self.data = {}
        self.cleared = False

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared = True
        self.state = None
        self.data = {}


# ----------------------------
# Helpers
# ----------------------------
async def _create_teacher(session, tg_id: int = 7000) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="Teacher", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


# ----------------------------
# Tests: admin root / students
# ----------------------------
@pytest.mark.asyncio
async def test_admin_root_shows_admin_menu(session):
    import app.handlers.admin.root as root_mod

    teacher = await _create_teacher(session, tg_id=7001)
    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await root_mod.admin_root(call, session)

    assert call.answered == 1
    assert msg.edits
    assert "Админка" in msg.edits[0][0]
    assert msg.edits[0][1].get("reply_markup") is not None


@pytest.mark.asyncio
async def test_admin_students_lists_students(session):
    import app.handlers.admin.students as students_mod

    teacher = await _create_teacher(session, tg_id=7002)

    # 2 ученика в базе
    s1 = Student(full_name="B Student", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    s2 = Student(full_name="A Student", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add_all([s1, s2])
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = AdminCb(action="students", page=1)
    await students_mod.admin_students(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    assert msg.edits[0][0] == "Ученики:"
    assert msg.edits[0][1].get("reply_markup") is not None


@pytest.mark.asyncio
async def test_admin_student_card_renders_text(session):
    import app.handlers.admin.students as students_mod

    teacher = await _create_teacher(session, tg_id=7003)
    st = Student(
        full_name="John Doe",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=1500,
    )
    session.add(st)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = AdminCb(action="student", student_id=st.id)
    await students_mod.admin_student_card(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    text = msg.edits[0][0]
    assert "Ученик: John Doe" in text
    assert "Тариф: single" in text
    assert msg.edits[0][1].get("reply_markup") is not None


# ----------------------------
# Tests: create student FSM
# ----------------------------
@pytest.mark.asyncio
async def test_create_student_full_flow_subscription(session):
    import app.handlers.admin.create_student as cs_mod

    teacher = await _create_teacher(session, tg_id=7010)

    # start
    msg0 = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg0.from_user, message=msg0)
    state = FakeFSMContext()

    await cs_mod.create_student_start(call, state, session)
    assert state.state == cs_mod.CreateStudentFSM.full_name
    assert call.answered == 1
    assert msg0.edits and "Создание ученика" in msg0.edits[0][0]

    # full_name
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="Ivan Ivanov")
    await cs_mod.create_student_full_name(msg1, state, session)
    assert state.state == cs_mod.CreateStudentFSM.tz
    assert "Выберите TZ ученика" in msg1.answers[-1][0]

    # tz -> теперь спрашиваем ссылку на доску
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="Europe/Moscow")
    await cs_mod.create_student_tz(msg2, state, session)
    assert state.state == cs_mod.CreateStudentFSM.board_url
    assert "ссылку на доску" in msg2.answers[-1][0].lower()

    # board_url (можно '-' чтобы пропустить)
    msg_board = FakeMessage(FakeFromUser(teacher.tg_id), text="-")
    await cs_mod.create_student_board_url(msg_board, state, session)
    assert state.state == cs_mod.CreateStudentFSM.billing
    assert "Выберите тариф" in msg_board.answers[-1][0]

    # billing subscription -> finalize_student
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="subscription")
    await cs_mod.create_student_billing(msg3, state, session)

    assert state.cleared is True
    assert msg3.answers
    assert "Ученик создан" in msg3.answers[-1][0]

    # в БД появился Student
    st = (await session.execute(select(Student).where(Student.full_name == "Ivan Ivanov"))).scalar_one()
    assert st.timezone == "Europe/Moscow"
    assert st.billing_mode == BillingMode.subscription
    assert st.price_per_lesson is None
    assert st.board_url is None


@pytest.mark.asyncio
async def test_create_student_single_requires_price_and_saves_price(session):
    import app.handlers.admin.create_student as cs_mod

    teacher = await _create_teacher(session, tg_id=7011)
    state = FakeFSMContext()

    # подготавливаем state как после ввода ФИО и TZ
    await state.update_data(full_name="Petr Petrov", tz="Europe/Moscow")
    await state.set_state(cs_mod.CreateStudentFSM.billing)

    # billing = single -> price step
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="single")
    await cs_mod.create_student_billing(msg1, state, session)
    assert state.state == cs_mod.CreateStudentFSM.price
    assert "Введите цену" in msg1.answers[-1][0]

    # price invalid
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="0")
    await cs_mod.create_student_price(msg2, state, session)
    assert "Введите число > 0" in msg2.answers[-1][0]
    assert state.state == cs_mod.CreateStudentFSM.price  # не ушли дальше

    # price ok (с запятой)
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="1500,50")
    await cs_mod.create_student_price(msg3, state, session)
    assert state.cleared is True

    st = (await session.execute(select(Student).where(Student.full_name == "Petr Petrov"))).scalar_one()
    assert st.billing_mode == BillingMode.single
    assert float(st.price_per_lesson) == pytest.approx(1500.50)


# ----------------------------
# Tests: keys (create_key + handler)
# ----------------------------
@pytest.mark.asyncio
async def test_admin_keys_creates_registration_key_student(monkeypatch, session):
    import app.handlers.admin.students as students_mod

    teacher = await _create_teacher(session, tg_id=7020)
    st = Student(full_name="Kid", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()

    # фиксируем токен, чтобы тест был детерминированный
    monkeypatch.setattr(students_mod.secrets, "token_urlsafe", lambda n: "KEY123")

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = AdminCb(action="keys_student", student_id=st.id)
    await students_mod.admin_keys(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    assert "`KEY123`" in msg.edits[0][0]  # markdown-код

    rk = (await session.execute(select(RegistrationKey).where(RegistrationKey.key == "KEY123"))).scalar_one()
    assert rk.student_id == st.id
    assert rk.role_target == Role.student
    assert rk.active is True
    assert rk.max_uses == 1


# ----------------------------
# Tests: student deletion
# ----------------------------
@pytest.mark.asyncio
async def test_student_delete_confirm_removes_student_user_keys_and_lesson_notifications(session):
    import app.handlers.admin.student_delete as del_mod

    teacher = await _create_teacher(session, tg_id=7030)

    # student user + student
    student_user = User(tg_id=90001, role=Role.student, name="S", timezone="Europe/Moscow")
    session.add(student_user)
    await session.flush()

    st = Student(
        full_name="To Delete",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        user_id=student_user.id,
    )
    session.add(st)
    await session.flush()

    # parent user + parent + link
    parent_user = User(tg_id=90002, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(parent_user)
    await session.flush()

    parent = Parent(user_id=parent_user.id, full_name="Parent")
    session.add(parent)
    await session.flush()

    session.add(ParentStudent(parent_id=parent.id, student_id=st.id))
    await session.flush()

    # lesson + notifications to delete
    lesson = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add(lesson)
    await session.flush()

    n1 = Notification(
        user_id=teacher.id,
        type="lesson_24h",
        entity_id=lesson.id,
        send_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        payload=None,
        status=NotificationStatus.pending,
    )
    n2 = Notification(
        user_id=teacher.id,
        type="lesson_1h",
        entity_id=lesson.id,
        send_at=datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc),
        payload=None,
        status=NotificationStatus.pending,
    )
    # notification другого типа — НЕ должна удалиться этим хендлером
    n3 = Notification(
        user_id=teacher.id,
        type="hw_graded",
        entity_id=999,
        send_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        payload="x",
        status=NotificationStatus.pending,
    )
    session.add_all([n1, n2, n3])

    # registration key
    rk = RegistrationKey(
        key="DELKEY",
        role_target=Role.student,
        student_id=st.id,
        expires_at=datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc),
        max_uses=1,
        used_count=0,
        active=True,
    )
    session.add(rk)

    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = AdminCb(action="student_delete_confirm", student_id=st.id)
    await del_mod.student_delete_confirm(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    assert "удалены" in msg.edits[0][0].lower()
    assert msg.edits[0][1].get("reply_markup") is not None  # admin_menu

    # Проверяем, что student и его user удалены
    st_db = (await session.execute(select(Student).where(Student.id == st.id))).scalar_one_or_none()
    assert st_db is None

    su_db = (await session.execute(select(User).where(User.id == student_user.id))).scalar_one_or_none()
    assert su_db is None

    # Родительский user должен удалиться (т.к. привязан только к этому student)
    pu_db = (await session.execute(select(User).where(User.id == parent_user.id))).scalar_one_or_none()
    assert pu_db is None

    # Ключ удалён
    rk_db = (await session.execute(select(RegistrationKey).where(RegistrationKey.key == "DELKEY"))).scalar_one_or_none()
    assert rk_db is None

    # lesson_* notifications удалены
    n1_db = (await session.execute(select(Notification).where(Notification.id == n1.id))).scalar_one_or_none()
    n2_db = (await session.execute(select(Notification).where(Notification.id == n2.id))).scalar_one_or_none()
    assert n1_db is None
    assert n2_db is None

    # hw_graded остался
    n3_db = (await session.execute(select(Notification).where(Notification.id == n3.id))).scalar_one_or_none()
    assert n3_db is not None
