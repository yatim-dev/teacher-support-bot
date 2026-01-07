from __future__ import annotations

import pytest
from datetime import date, time, datetime, timezone

from sqlalchemy import select, func

from app.models import User, Role, Student, BillingMode, Lesson, LessonStatus, ScheduleRule
from app.callbacks import AdminCb, FsmNavCb


# ----------------------------
# Fakes
# ----------------------------
class FakeFromUser:
    def __init__(self, user_id: int, full_name: str = "Teacher"):
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
        self.alerts: list[tuple[str, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answered += 1
        if text is not None:
            self.alerts.append((text, show_alert))


class FakeFSMContext:
    def __init__(self):
        self._state: str | None = None
        self._data: dict = {}
        self.cleared = False

    async def set_state(self, state):
        # aiogram State имеет .state (строку вида "Group:field")
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


# ----------------------------
# Helpers
# ----------------------------
async def create_teacher(session, tg_id: int = 5000) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


async def create_student(session, full_name="S", tz="Europe/Moscow") -> Student:
    st = Student(full_name=full_name, timezone=tz, billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    return st


# ----------------------------
# choose_type.py
# ----------------------------
@pytest.mark.asyncio
async def test_lesson_add_choose_requires_student_id(session):
    import app.handlers.admin.lessons_add.choose_type as mod

    teacher = await create_teacher(session, tg_id=5001)
    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg.from_user, msg)

    cb = AdminCb(action="lessons_add", student_id=None)
    await mod.lesson_add_choose(call, cb, session)

    assert call.answered == 1
    assert call.alerts == [("Не выбран ученик", True)]
    assert msg.edits == []


@pytest.mark.asyncio
async def test_lesson_add_choose_renders_menu(session):
    import app.handlers.admin.lessons_add.choose_type as mod

    teacher = await create_teacher(session, tg_id=5002)
    st = await create_student(session)

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg.from_user, msg)

    cb = AdminCb(action="lessons_add", student_id=st.id)
    await mod.lesson_add_choose(call, cb, session)

    assert call.answered == 1
    assert msg.edits
    text, kwargs = msg.edits[0]
    assert "Добавить занятие" in text
    assert kwargs.get("reply_markup") is not None


# ----------------------------
# nav.py
# ----------------------------
@pytest.mark.asyncio
async def test_fsm_add_rule_nav_back_steps(monkeypatch, session):
    import app.handlers.admin.lessons_add.nav as nav_mod
    from app.handlers.admin.lessons_add.states import AddRuleFSM

    teacher = await create_teacher(session, tg_id=5003)

    # add_rule nav не проверяет user/teacher, там только state
    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg.from_user, msg)
    state = FakeFSMContext()
    await state.update_data(student_id=123)

    # current = start_date -> back => duration
    await state.set_state(AddRuleFSM.start_date)
    cb = FsmNavCb(action="back", flow="add_rule", student_id=123)
    await nav_mod.fsm_add_rule_nav(call, cb, state)
    assert await state.get_state() == AddRuleFSM.duration.state
    assert "длительность" in msg.edits[-1][0].lower()

    # current = duration -> back => time_local
    await state.set_state(AddRuleFSM.duration)
    await nav_mod.fsm_add_rule_nav(call, cb, state)
    assert await state.get_state() == AddRuleFSM.time_local.state
    assert "время" in msg.edits[-1][0].lower()

    # current = time_local -> back => weekday
    await state.set_state(AddRuleFSM.time_local)
    await nav_mod.fsm_add_rule_nav(call, cb, state)
    assert await state.get_state() == AddRuleFSM.weekday.state
    assert "день недели" in msg.edits[-1][0].lower()


@pytest.mark.asyncio
async def test_fsm_add_rule_nav_cancel_clears_and_goes_to_student_card(session):
    import app.handlers.admin.lessons_add.nav as nav_mod

    teacher = await create_teacher(session, tg_id=5004)
    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg.from_user, msg)
    state = FakeFSMContext()

    await state.update_data(student_id=777)
    cb = FsmNavCb(action="cancel", flow="add_rule", student_id=777)

    await nav_mod.fsm_add_rule_nav(call, cb, state)

    assert state.cleared is True
    assert msg.edits
    assert "Отменено" in msg.edits[-1][0]
    assert msg.edits[-1][1].get("reply_markup") is not None


@pytest.mark.asyncio
async def test_fsm_add_single_nav_cancel_clears(session):
    import app.handlers.admin.lessons_add.nav as nav_mod
    from app.handlers.admin.lessons_add.states import AddSingleLessonFSM

    teacher = await create_teacher(session, tg_id=5005)
    st = await create_student(session)

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg.from_user, msg)
    state = FakeFSMContext()
    await state.update_data(student_id=st.id)
    await state.set_state(AddSingleLessonFSM.time_)

    cb = FsmNavCb(action="cancel", flow="add_single", student_id=st.id)
    await nav_mod.fsm_add_single_nav(call, cb, state, session)

    assert state.cleared is True
    assert "Отменено" in msg.edits[-1][0]
    assert msg.edits[-1][1].get("reply_markup") is not None


# ----------------------------
# rule.py
# ----------------------------
@pytest.mark.asyncio
async def test_add_rule_full_flow_creates_rule(monkeypatch, session):
    import app.handlers.admin.lessons_add.rule as rule_mod
    from app.handlers.admin.lessons_add.states import AddRuleFSM

    teacher = await create_teacher(session, tg_id=5010)
    st = await create_student(session)

    # мокнем внешние действия, чтобы тест был быстрым и стабильным
    async def fake_generate_lessons_for_student(session_, student_id: int):
        return 123

    async def fake_plan_job():
        return None

    monkeypatch.setattr(rule_mod, "generate_lessons_for_student", fake_generate_lessons_for_student)
    monkeypatch.setattr(rule_mod, "plan_lesson_notifications_job", fake_plan_job)

    state = FakeFSMContext()

    # start
    msg0 = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg0.from_user, msg0)
    await rule_mod.add_rule_start(call, AdminCb(action="add_rule", student_id=st.id), state, session)

    assert await state.get_state() == AddRuleFSM.weekday.state
    assert msg0.edits
    assert "Еженедельное занятие" in msg0.edits[0][0]

    # weekday invalid
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="8")
    await rule_mod.add_rule_weekday(msg1, state, session)
    assert "Введите число 1..7" in msg1.answers[-1][0]

    # weekday ok (1=ПН -> 0)
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="1")
    await rule_mod.add_rule_weekday(msg2, state, session)
    assert await state.get_state() == AddRuleFSM.time_local.state

    # time invalid
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="xx")
    await rule_mod.add_rule_time(msg3, state, session)
    assert "Формат HH:MM" in msg3.answers[-1][0]

    # time ok
    msg4 = FakeMessage(FakeFromUser(teacher.tg_id), text="16:30")
    await rule_mod.add_rule_time(msg4, state, session)
    assert await state.get_state() == AddRuleFSM.duration.state

    # duration invalid
    msg5 = FakeMessage(FakeFromUser(teacher.tg_id), text="0")
    await rule_mod.add_rule_duration(msg5, state, session)
    assert "1..600" in msg5.answers[-1][0]

    # duration ok
    msg6 = FakeMessage(FakeFromUser(teacher.tg_id), text="60")
    await rule_mod.add_rule_duration(msg6, state, session)
    assert await state.get_state() == AddRuleFSM.start_date.state

    # start_date invalid
    msg7 = FakeMessage(FakeFromUser(teacher.tg_id), text="2026/01/10")
    await rule_mod.add_rule_start_date(msg7, state, session)
    assert "Формат YYYY-MM-DD" in msg7.answers[-1][0]

    # start_date ok -> create rule
    msg8 = FakeMessage(FakeFromUser(teacher.tg_id), text="2026-01-10")
    await rule_mod.add_rule_start_date(msg8, state, session)

    assert state.cleared is True
    assert msg8.answers
    assert "Еженедельное правило добавлено" in msg8.answers[-1][0]

    # rule created in DB
    r = (await session.execute(select(ScheduleRule).where(ScheduleRule.student_id == st.id))).scalar_one()
    assert r.active is True
    assert r.duration_min == 60
    assert r.time_local == time(16, 30)
    assert r.weekday == 0
    assert r.start_date == date(2026, 1, 10)


@pytest.mark.asyncio
async def test_add_single_full_flow_creates_lesson_and_handles_conflict(monkeypatch, session):
    import app.handlers.admin.lessons_add.single as single_mod
    from app.handlers.admin.lessons_add.states import AddSingleLessonFSM
    from app.handlers.admin.common import local_to_utc

    teacher = await create_teacher(session, tg_id=5020)
    st = await create_student(session, tz="Europe/Moscow")
    st_id = st.id  # важно: сохранить int

    async def fake_plan_job():
        return None

    monkeypatch.setattr(single_mod, "plan_lesson_notifications_job", fake_plan_job)

    state = FakeFSMContext()

    # start
    msg0 = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(msg0.from_user, msg0)
    await single_mod.add_single_start(call, AdminCb(action="add_single", student_id=st_id), state, session)
    assert await state.get_state() == AddSingleLessonFSM.date_.state
    assert "Разовое занятие" in msg0.edits[0][0]

    # date invalid
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="2026/01/10")
    await single_mod.add_single_date(msg1, state, session)
    assert "Формат YYYY-MM-DD" in msg1.answers[-1][0]

    # date ok
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="2026-01-10")
    await single_mod.add_single_date(msg2, state, session)
    assert await state.get_state() == AddSingleLessonFSM.time_.state

    # time invalid
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="xx")
    await single_mod.add_single_time(msg3, state, session)
    assert "Формат HH:MM" in msg3.answers[-1][0]

    # time ok
    msg4 = FakeMessage(FakeFromUser(teacher.tg_id), text="16:30")
    await single_mod.add_single_time(msg4, state, session)
    assert await state.get_state() == AddSingleLessonFSM.duration.state

    # duration invalid
    msg5 = FakeMessage(FakeFromUser(teacher.tg_id), text="0")
    await single_mod.add_single_duration(msg5, state, session)
    assert "1..600" in msg5.answers[-1][0]

    # duration ok -> создаст lesson
    msg6 = FakeMessage(FakeFromUser(teacher.tg_id), text="60")
    await single_mod.add_single_duration(msg6, state, session)
    assert state.cleared is True
    assert "Разовое занятие создано" in msg6.answers[-1][0]

    # lesson created (проверяем ТУТ)
    cnt = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st_id)
    )).scalar_one()
    assert cnt == 1

    # --- теперь проверим ветку IntegrityError (конфликт) ---
    state2 = FakeFSMContext()
    await state2.update_data(student_id=st_id, date_=date(2026, 1, 10), time_=time(16, 30))
    await state2.set_state(AddSingleLessonFSM.duration)

    start_at = local_to_utc("Europe/Moscow", date(2026, 1, 10), time(16, 30))
    existing = (await session.execute(
        select(Lesson).where(Lesson.student_id == st_id, Lesson.start_at == start_at)
    )).scalar_one()
    assert existing is not None

    msg7 = FakeMessage(FakeFromUser(teacher.tg_id), text="60")
    await single_mod.add_single_duration(msg7, state2, session)

    assert "уже есть занятие" in msg7.answers[-1][0].lower()

    cnt2 = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.student_id == st_id)
    )).scalar_one()
    assert cnt2 == 1

