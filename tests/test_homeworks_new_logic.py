import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.callbacks import AdminCb, HomeworkCb
from app.models import User, Role, Student, BillingMode, Homework


# ---------------- fakes ----------------
class FakeFromUser:
    def __init__(self, user_id: int, full_name: str = "X"):
        self.id = user_id
        self.full_name = full_name


class FakeMessage:
    def __init__(self, from_user: FakeFromUser, text: str | None = None):
        self.from_user = from_user
        self.text = text
        self.edits: list[tuple[str, dict]] = []
        self.answers: list[tuple[str, dict]] = []

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, {"reply_markup": reply_markup, **kwargs}))

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, {"reply_markup": reply_markup, **kwargs}))


class FakeCallbackQuery:
    def __init__(self, tg_id: int):
        self.from_user = SimpleNamespace(id=tg_id)
        self.message = FakeMessage(FakeFromUser(tg_id))
        self.answer = AsyncMock()
        # для student "done" в homework_menu
        self.bot = SimpleNamespace(send_message=AsyncMock())


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


async def mk_teacher(session, tg_id: int = 9001) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


async def mk_student_user_and_student(session, tg_id: int = 9002, tz: str = "Europe/Moscow") -> tuple[User, Student]:
    u = User(tg_id=tg_id, role=Role.student, name="S", timezone=tz)
    session.add(u)
    await session.flush()

    st = Student(full_name="Student", timezone=tz, billing_mode=BillingMode.subscription, user_id=u.id)
    session.add(st)
    await session.commit()
    await session.refresh(st)
    return u, st


def _all_callback_data(markup) -> list[str]:
    if markup is None:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]


# ---------------- tests ----------------
@pytest.mark.asyncio
async def test_admin_homeworks_opens_list_and_has_create_and_back_buttons(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await mk_teacher(session, tg_id=91001)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    call = FakeCallbackQuery(teacher.tg_id)
    await hw_mod.admin_student_homeworks(call, AdminCb(action="homeworks", student_id=st.id, page=1), session)

    assert call.message.edits
    text, kwargs = call.message.edits[-1]
    assert "Домашние задания" in text

    cds = _all_callback_data(kwargs["reply_markup"])
    assert AdminCb(action="hw_create", student_id=st.id, page=1).pack() in cds
    assert AdminCb(action="student", student_id=st.id, page=1).pack() in cds  # "Назад" в карточку ученика

    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_hw_create_fsm_creates_homework_and_due_at_dash_finishes(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await mk_teacher(session, tg_id=91002)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    state = FakeFSMContext()

    # старт "создать ДЗ"
    call = FakeCallbackQuery(teacher.tg_id)
    await hw_mod.admin_hw_create_start(call, AdminCb(action="hw_create", student_id=st.id, page=1), state, session)
    assert state.state == hw_mod.HomeworkFSM.title
    assert state.data["student_id"] == st.id

    # title
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="HW Title")
    await hw_mod.hw_set_title(msg1, state, session)
    assert state.state == hw_mod.HomeworkFSM.description

    # description -> создаёт Homework и переводит на due_at
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="Do exercises")
    await hw_mod.hw_set_description(msg2, state, session)
    assert state.state == hw_mod.HomeworkFSM.due_at
    assert "homework_id" in state.data

    hw_id = state.data["homework_id"]
    hw = (await session.execute(select(Homework).where(Homework.id == hw_id))).scalar_one()
    assert hw.student_id == st.id
    assert hw.title == "HW Title"
    assert hw.description == "Do exercises"

    # due_at "-" -> finish
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="-")
    await hw_mod.hw_set_due_at(msg3, state, session)
    assert state.cleared is True

    hw2 = (await session.execute(select(Homework).where(Homework.id == hw_id))).scalar_one()
    assert hw2.due_at is None


@pytest.mark.asyncio
async def test_hw_set_due_at_parses_local_time_to_utc(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await mk_teacher(session, tg_id=91003)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    hw = Homework(student_id=st.id, title="HW", description="Desc", grade=None, graded_at=None, due_at=None, student_done_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    state = FakeFSMContext()
    await state.update_data(homework_id=hw.id, student_id=st.id, offset=0)
    await state.set_state(hw_mod.HomeworkFSM.due_at)

    # 2026-02-11 12:00 MSK => 09:00 UTC
    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="2026-02-11 12:00")
    await hw_mod.hw_set_due_at(msg, state, session)

    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.due_at is not None
    assert hw2.due_at.tzinfo is not None
    assert hw2.due_at.astimezone(timezone.utc).hour == 9


@pytest.mark.asyncio
async def test_homework_menu_teacher_back_returns_to_list(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await mk_teacher(session, tg_id=91004)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    hw = Homework(student_id=st.id, title="HW", description="Desc", grade=None, graded_at=None, due_at=None, student_done_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    call = FakeCallbackQuery(teacher.tg_id)
    state = FakeFSMContext()

    cb = HomeworkCb(action="back", homework_id=hw.id, student_id=st.id, offset=0)
    await hw_mod.homework_menu(call, cb, state, session)

    assert call.message.edits
    text, _kwargs = call.message.edits[-1]
    assert "Домашние задания" in text
    call.answer.assert_awaited()
