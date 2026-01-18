import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.callbacks import AdminCb
from app.models import User, Role, Student, BillingMode


# ----------------- fakes -----------------
class FakeFromUser:
    def __init__(self, user_id: int, full_name: str = "X"):
        self.id = user_id
        self.full_name = full_name


class FakeMessage:
    """
    Универсальный fake Message:
    - для FSM-хендлеров: message.text + message.answer(...)
    - для callback-хендлеров: message.edit_text(...)
    """
    def __init__(self, from_user: FakeFromUser | None = None, text: str = ""):
        self.from_user = from_user or FakeFromUser(0)
        self.text = text

        self.answers: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, {"reply_markup": reply_markup, **kwargs}))

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, {"reply_markup": reply_markup, **kwargs}))


class FakeCallbackQuery:
    def __init__(self, user_id: int):
        fu = FakeFromUser(user_id)
        self.from_user = SimpleNamespace(id=user_id, full_name=fu.full_name)
        self.message = FakeMessage(from_user=fu)
        self.answer = AsyncMock()


class FakeFSMContext:
    def __init__(self):
        self._state: str | None = None
        self._data: dict = {}
        self.cleared = False

    async def set_state(self, state):
        # aiogram State хранит строку в .state
        self._state = state.state if hasattr(state, "state") else state

    async def get_state(self):
        return self._state

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self.cleared = True
        self._state = None
        self._data = {}


async def mk_teacher(session, tg_id: int = 9001) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


# ----------------- tests: create_student FSM -----------------
@pytest.mark.asyncio
async def test_create_student_tz_goes_to_board_url_and_does_not_ask_billing(session):
    """
    Защищаемся от бага: после TZ не должно появляться сообщение "выберите тариф".
    """
    from app.handlers.admin.create_student import create_student_tz, CreateStudentFSM
    from app.keyboards import TZ_LIST

    teacher = await mk_teacher(session, tg_id=9101)
    state = FakeFSMContext()

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text=TZ_LIST[0])
    await create_student_tz(msg, state, session)

    assert await state.get_state() == CreateStudentFSM.board_url.state
    assert msg.answers, "Должно быть хотя бы одно сообщение после ввода TZ"

    # Должна быть просьба про доску (не привязываемся к точной фразе 1-в-1)
    assert any(("ссыл" in t.lower() and "доск" in t.lower()) for t, _ in msg.answers)

    # Главное: не должно быть сообщения про тариф на этом шаге
    assert not any("выберите тариф" in t.lower() for t, _ in msg.answers)


@pytest.mark.asyncio
async def test_create_student_board_url_invalid_url_rejected(session):
    from app.handlers.admin.create_student import create_student_board_url, CreateStudentFSM

    teacher = await mk_teacher(session, tg_id=9102)
    state = FakeFSMContext()
    await state.set_state(CreateStudentFSM.board_url)

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="miro.com/xxx")  # без https://
    await create_student_board_url(msg, state, session)

    assert msg.answers
    assert "http" in msg.answers[-1][0].lower()
    assert await state.get_state() == CreateStudentFSM.board_url.state


@pytest.mark.asyncio
async def test_create_student_finalize_saves_board_url_for_subscription(session):
    """
    Happy-path: full_name -> tz -> board_url -> billing(subscription).
    Проверяем, что board_url сохранился у Student.
    """
    from app.handlers.admin.create_student import (
        create_student_full_name,
        create_student_tz,
        create_student_board_url,
        create_student_billing,
        CreateStudentFSM,
    )
    from app.keyboards import TZ_LIST

    teacher = await mk_teacher(session, tg_id=9103)
    state = FakeFSMContext()

    # full_name
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="Тестовый Ученик")
    await create_student_full_name(msg1, state, session)
    assert await state.get_state() == CreateStudentFSM.tz.state

    # tz
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text=TZ_LIST[0])
    await create_student_tz(msg2, state, session)
    assert await state.get_state() == CreateStudentFSM.board_url.state

    # board_url
    url = "https://miro.com/app/board/test"
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text=url)
    await create_student_board_url(msg3, state, session)
    assert await state.get_state() == CreateStudentFSM.billing.state

    # billing -> subscription -> finalize
    msg4 = FakeMessage(FakeFromUser(teacher.tg_id), text="subscription")
    await create_student_billing(msg4, state, session)

    assert state.cleared is True

    st = (await session.execute(select(Student).where(Student.full_name == "Тестовый Ученик"))).scalar_one()
    assert st.board_url == url
    assert st.billing_mode == BillingMode.subscription


# ----------------- tests: admin student card shows board_url -----------------
@pytest.mark.asyncio
async def test_admin_student_card_contains_board_url(session):
    import app.handlers.admin.students as mod

    teacher = await mk_teacher(session, tg_id=9201)

    st = Student(
        full_name="S",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        board_url="https://example.com/board/123",
    )
    session.add(st)
    await session.commit()
    st_id = st.id

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = AdminCb(action="student", student_id=st_id, page=1)

    await mod.admin_student_card(call, cb, session)

    assert call.message.edits
    text, _kwargs = call.message.edits[-1]
    assert "Доска:" in text
    assert "https://example.com/board/123" in text

    call.answer.assert_awaited()


# ----------------- tests: student_schedule shows board_url -----------------
@pytest.mark.asyncio
async def test_student_schedule_shows_board_url_in_text(session):
    import app.handlers.student as student_mod

    u = User(tg_id=9301, role=Role.student, name="SUser", timezone="Europe/Moscow")
    session.add(u)
    await session.flush()

    st = Student(
        full_name="Student",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        user_id=u.id,
        board_url="https://example.com/board/student",
    )
    session.add(st)
    await session.commit()

    call = FakeCallbackQuery(user_id=u.tg_id)

    await student_mod.student_schedule(call, session)

    assert call.message.edits
    text, _kwargs = call.message.edits[-1]
    assert "Ваша доска:" in text
    assert "https://example.com/board/student" in text

    call.answer.assert_awaited()


# ----------------- tests: board edit FSM -----------------
@pytest.mark.asyncio
async def test_board_edit_flow_sets_new_url(session):
    """
    Учитель открывает редактирование, вводит новую ссылку -> сохраняется в Student.
    """
    import app.handlers.admin.board as board_mod
    from app.callbacks import BoardCb
    from app.handlers.admin.board import EditBoardFSM

    teacher = await mk_teacher(session, tg_id=9401)

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription, board_url=None)
    session.add(st)
    await session.commit()
    st_id = st.id

    state = FakeFSMContext()

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    await board_mod.board_edit_start(call, BoardCb(action="edit", student_id=st_id), state, session)
    assert await state.get_state() == EditBoardFSM.url.state
    assert call.message.edits

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="https://miro.com/app/board/new")
    await board_mod.board_edit_set(msg, state, session)

    st2 = (await session.execute(select(Student).where(Student.id == st_id))).scalar_one()
    assert st2.board_url == "https://miro.com/app/board/new"


@pytest.mark.asyncio
async def test_board_edit_flow_dash_clears_url(session):
    import app.handlers.admin.board as board_mod
    from app.callbacks import BoardCb
    from app.handlers.admin.board import EditBoardFSM

    teacher = await mk_teacher(session, tg_id=9402)

    st = Student(
        full_name="S",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.subscription,
        board_url="https://example.com/board/old",
    )
    session.add(st)
    await session.commit()
    st_id = st.id

    state = FakeFSMContext()
    call = FakeCallbackQuery(user_id=teacher.tg_id)
    await board_mod.board_edit_start(call, BoardCb(action="edit", student_id=st_id), state, session)
    assert await state.get_state() == EditBoardFSM.url.state

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="-")
    await board_mod.board_edit_set(msg, state, session)

    st2 = (await session.execute(select(Student).where(Student.id == st_id))).scalar_one()
    assert st2.board_url is None
