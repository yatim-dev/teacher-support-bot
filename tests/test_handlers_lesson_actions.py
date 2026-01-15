from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from sqlalchemy import select, func

from app.models import (
    User, Role,
    Student, BillingMode,
    Lesson, LessonStatus,
    ScheduleRule,
    Homework,
    Parent, ParentStudent,
    Notification, NotificationStatus,
    LessonCharge, ChargeStatus,
)
from app.callbacks import LessonCb, HomeworkCb, AdminCb


# ---------- Fakes ----------
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

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, {"reply_markup": reply_markup, **kwargs}))

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, {"reply_markup": reply_markup, **kwargs}))


class FakeCallbackQuery:
    def __init__(self, from_user: FakeFromUser, message: FakeMessage):
        self.from_user = from_user
        self.message = message
        self.answer = AsyncMock()


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


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, tg_id: int, text: str):
        self.sent.append((tg_id, text))


# ---------- helpers ----------
async def create_teacher(session, tg_id: int = 6000) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="Teacher", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


# ---------- tests ----------
@pytest.mark.asyncio
async def test_admin_lessons_requires_student_id(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6001)

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await lessons_mod.admin_lessons(call, AdminCb(action="lessons", student_id=None), session)

    # должен ответить show_alert=True с текстом "Не выбран ученик"
    call.answer.assert_awaited()
    answered = call.answer.await_args
    assert answered.args and answered.args[0] == "Не выбран ученик"
    assert answered.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_render_lesson_card_when_no_lessons(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6002)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await lessons_mod.render_lesson_card(call, session, student_id=st.id, offset=0)

    assert msg.edits
    text, _kwargs = msg.edits[0]
    assert "Ближайших уроков нет" in text


@pytest.mark.asyncio
async def test_lesson_action_next_prev_renders(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6003)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    l1 = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    l2 = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
    )
    session.add_all([l1, l2])
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    # next
    cb_next = LessonCb(action="next", lesson_id=l1.id, student_id=st.id, offset=0)
    await lessons_mod.lesson_action(call, cb_next, session, bot=FakeBot())

    assert call.answer.await_count == 1
    assert msg.edits
    assert "Урок:" in msg.edits[-1][0]

    # prev (с offset=1 возвращает на 0)
    cb_prev = LessonCb(action="prev", lesson_id=l2.id, student_id=st.id, offset=1)
    await lessons_mod.lesson_action(call, cb_prev, session, bot=FakeBot())

    assert call.answer.await_count == 2
    assert "Урок:" in msg.edits[-1][0]


@pytest.mark.asyncio
async def test_lesson_action_cancel_single_deletes_lesson(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6004)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    l = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=None,  # важно: чтобы считалось разовым
    )
    session.add(l)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = LessonCb(action="cancel", lesson_id=l.id, student_id=st.id, offset=0)
    await lessons_mod.lesson_action(call, cb, session, bot=FakeBot())

    call.answer.assert_awaited()
    assert msg.edits
    assert "Разовое занятие отменено" in msg.edits[-1][0]

    l_db = (await session.execute(select(Lesson).where(Lesson.id == l.id))).scalar_one_or_none()
    assert l_db is None


@pytest.mark.asyncio
async def test_lesson_action_cancel_recurring_marks_canceled(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6005)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    rule = ScheduleRule(student_id=st.id, weekday=0, time_local=datetime.now().time(), duration_min=60, start_date=datetime.now().date(), end_date=None, active=True)
    session.add(rule)
    await session.flush()

    l = Lesson(
        student_id=st.id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=rule.id,
    )
    session.add(l)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = LessonCb(action="cancel", lesson_id=l.id, student_id=st.id, offset=0)
    await lessons_mod.lesson_action(call, cb, session, bot=FakeBot())

    assert "отменено" in msg.edits[-1][0].lower()

    l_db = (await session.execute(select(Lesson).where(Lesson.id == l.id))).scalar_one()
    assert l_db.status == LessonStatus.canceled


@pytest.mark.asyncio
async def test_lesson_action_delete_series_deletes_future_lessons_and_rule(session):
    import app.handlers.admin.lessons as lessons_mod

    teacher = await create_teacher(session, tg_id=6006)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    rule = ScheduleRule(student_id=st.id, weekday=0, time_local=datetime.now().time(), duration_min=60, start_date=datetime.now().date(), end_date=None, active=True)
    session.add(rule)
    await session.flush()

    now = datetime.now(timezone.utc)

    past = Lesson(student_id=st.id, start_at=now - timedelta(days=1), duration_min=60, status=LessonStatus.planned, source_rule_id=rule.id)
    future1 = Lesson(student_id=st.id, start_at=now + timedelta(days=1), duration_min=60, status=LessonStatus.planned, source_rule_id=rule.id)
    future2 = Lesson(student_id=st.id, start_at=now + timedelta(days=2), duration_min=60, status=LessonStatus.planned, source_rule_id=rule.id)
    session.add_all([past, future1, future2])
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    cb = LessonCb(action="delete_series", lesson_id=future1.id, student_id=st.id, offset=0)
    await lessons_mod.lesson_action(call, cb, session, bot=FakeBot())

    # правило удалено
    rule_db = (await session.execute(select(ScheduleRule).where(ScheduleRule.id == rule.id))).scalar_one_or_none()
    assert rule_db is None

    # будущие уроки удалены
    fcnt = (await session.execute(select(func.count()).select_from(Lesson).where(Lesson.source_rule_id == rule.id, Lesson.start_at >= now))).scalar_one()
    assert fcnt == 0

    # прошлый урок сохранён
    p = (await session.execute(select(Lesson).where(Lesson.id == past.id))).scalar_one_or_none()
    assert p is not None


@pytest.mark.asyncio
async def test_lesson_action_done_single_creates_charge_and_shows_pay_button(session):
    import app.handlers.admin.lessons as lessons_mod
    from app.callbacks import LessonPayCb

    teacher = await create_teacher(session, tg_id=6007)

    parent_user = User(tg_id=70070, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add(parent_user)
    await session.flush()

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    p = Parent(user_id=parent_user.id, full_name="Parent")
    session.add(p)
    await session.flush()
    session.add(ParentStudent(parent_id=p.id, student_id=st_id))

    l = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=None,
    )
    session.add(l)
    await session.commit()
    lesson_id = l.id

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)
    bot = FakeBot()

    cb = LessonCb(action="done", lesson_id=lesson_id, student_id=st_id, offset=0)
    await lessons_mod.lesson_action(call, cb, session, bot=bot)

    # charge создан
    ch = (await session.execute(select(LessonCharge).where(LessonCharge.lesson_id == lesson_id))).scalar_one()
    assert ch.status == ChargeStatus.pending

    # карточка перерисована, статус "не оплачено"
    assert msg.edits
    text, kwargs = msg.edits[-1]
    assert "не оплачено" in text.lower()

    # есть кнопка "Урок оплачен" (теперь по lesson_id, а не по charge_id)
    markup = kwargs.get("reply_markup")
    assert markup is not None
    all_cb = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert LessonPayCb(action="paid", lesson_id=lesson_id, student_id=st_id, offset=0).pack() in all_cb

    assert bot.sent  # родителю отправили сообщение


@pytest.mark.asyncio
async def test_lesson_pay_paid_marks_paid_and_rerenders_cards(session):
    import app.handlers.admin.payments as payments_mod
    from app.callbacks import LessonPayCb

    teacher = await create_teacher(session, tg_id=6008)

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    l = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.done,
        source_rule_id=None,
    )
    session.add(l)
    await session.flush()

    # pending charge уже есть
    ch = LessonCharge(lesson_id=l.id, student_id=st_id, amount=1000.0, status=ChargeStatus.pending)
    session.add(ch)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    # оплата теперь отмечается по lesson_id
    await payments_mod.lesson_pay_action(
        call,
        LessonPayCb(action="paid", lesson_id=l.id, student_id=st_id, offset=0),
        session,
    )

    ch2 = (await session.execute(select(LessonCharge).where(LessonCharge.lesson_id == l.id))).scalar_one()
    assert ch2.status == ChargeStatus.paid
    assert ch2.paid_at is not None

    # после оплаты идёт render_lesson_card; если других уроков нет — будет "Ближайших уроков нет."
    assert msg.edits
    last_text = msg.edits[-1][0]
    assert (last_text == "Ближайших уроков нет.") or ("Урок:" in last_text)




# ---------- Homework flow ----------
@pytest.mark.asyncio
async def test_homework_menu_edit_sets_fsm_title(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await create_teacher(session, tg_id=6010)

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    # ДЗ уже существует (чтобы было что "редактировать")
    hw = Homework(
        student_id=st.id,
        title="Old",
        description="Old",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)
    state = FakeFSMContext()

    cb = HomeworkCb(action="edit", homework_id=hw.id, student_id=st.id, offset=0)
    await hw_mod.homework_menu(call, cb, state, session)

    assert state.state == hw_mod.HomeworkFSM.title
    assert state.data["homework_id"] == hw.id
    assert state.data["student_id"] == st.id
    assert "Введите название" in msg.edits[-1][0]
    call.answer.assert_awaited()


@pytest.mark.asyncio
async def test_hw_set_title_and_description_creates_homework(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await create_teacher(session, tg_id=6011)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    state = FakeFSMContext()
    await state.update_data(student_id=st.id, offset=0)

    # title too short
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="A")
    await hw_mod.hw_set_title(msg1, state, session)
    assert msg1.answers
    assert "слишком короткое" in msg1.answers[-1][0].lower()

    # title ok -> go to description
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="HW Title")
    await hw_mod.hw_set_title(msg2, state, session)
    assert state.state == hw_mod.HomeworkFSM.description

    # description too short
    msg3 = FakeMessage(FakeFromUser(teacher.tg_id), text="x")
    await hw_mod.hw_set_description(msg3, state, session)
    assert msg3.answers
    assert "слишком короткое" in msg3.answers[-1][0].lower()

    # description ok -> homework created -> go to due_at
    msg4 = FakeMessage(FakeFromUser(teacher.tg_id), text="Do exercises")
    await hw_mod.hw_set_description(msg4, state, session)

    assert state.state == hw_mod.HomeworkFSM.due_at
    assert "homework_id" in state.data

    hw_id = state.data["homework_id"]
    hw = (await session.execute(select(Homework).where(Homework.id == hw_id))).scalar_one()
    assert hw.title == "HW Title"
    assert hw.description == "Do exercises"
    assert hw.grade is None


@pytest.mark.asyncio
async def test_hw_set_due_at_dash_finishes_flow(session):
    """
    Завершение создания: на шаге due_at вводим '-' -> due_at=None и state.clear()
    """
    import app.handlers.admin.homeworks as hw_mod

    teacher = await create_teacher(session, tg_id=60115)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    hw = Homework(
        student_id=st.id,
        title="HW",
        description="Desc",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    state = FakeFSMContext()
    await state.update_data(homework_id=hw.id, student_id=st.id, offset=0)
    await state.set_state(hw_mod.HomeworkFSM.due_at)

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="-")
    await hw_mod.hw_set_due_at(msg, state, session)

    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.due_at is None
    assert state.cleared is True


@pytest.mark.asyncio
async def test_hw_set_grade_invalid_and_then_creates_notifications(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await create_teacher(session, tg_id=6012)

    # student user + parent user for notifications
    student_user = User(tg_id=91001, role=Role.student, name="S", timezone="Europe/Moscow")
    parent_user = User(tg_id=91002, role=Role.parent, name="P", timezone="Europe/Moscow")
    session.add_all([student_user, parent_user])
    await session.flush()

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription, user_id=student_user.id)
    session.add(st)
    await session.flush()

    p = Parent(user_id=parent_user.id, full_name="Parent")
    session.add(p)
    await session.flush()
    session.add(ParentStudent(parent_id=p.id, student_id=st.id))
    await session.flush()

    hw = Homework(
        student_id=st.id,
        title="HW",
        description="Desc",
        grade=None,
        graded_at=None,
        due_at=None,
        student_done_at=None,
    )
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    state = FakeFSMContext()
    await state.update_data(homework_id=hw.id, student_id=st.id, offset=0)
    await state.set_state(hw_mod.HomeworkFSM.grade)

    # invalid grade
    msg1 = FakeMessage(FakeFromUser(teacher.tg_id), text="11")
    await hw_mod.hw_set_grade(msg1, state, session)
    assert msg1.answers
    assert ("1–10" in msg1.answers[-1][0]) or ("1-10" in msg1.answers[-1][0])

    # valid grade
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id), text="9")
    await hw_mod.hw_set_grade(msg2, state, session)

    hw2 = (await session.execute(select(Homework).where(Homework.id == hw.id))).scalar_one()
    assert hw2.grade == 9
    assert hw2.graded_at is not None

    notifs = (await session.execute(select(Notification).where(Notification.type == "hw_graded"))).scalars().all()
    assert len(notifs) == 2
    assert all(n.status == NotificationStatus.pending for n in notifs)
    assert all(n.entity_id == hw.id for n in notifs)

    assert state.cleared is True


@pytest.mark.asyncio
async def test_hw_set_grade_without_homework_requires_create_first(session):
    import app.handlers.admin.homeworks as hw_mod

    teacher = await create_teacher(session, tg_id=6013)
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.commit()
    await session.refresh(st)

    state = FakeFSMContext()
    await state.update_data(homework_id=999999, student_id=st.id, offset=0)  # несуществующее ДЗ
    await state.set_state(hw_mod.HomeworkFSM.grade)

    msg = FakeMessage(FakeFromUser(teacher.tg_id), text="5")
    await hw_mod.hw_set_grade(msg, state, session)

    assert msg.answers
    t = msg.answers[-1][0].lower()
    assert ("дз" in t) or ("сначала" in t) or ("не найден" in t)
