import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from app.models import (
    Student, BillingMode,
    Lesson, LessonStatus,
    LessonCharge, ChargeStatus,
    User, Role,
    StudentBalance,
)
from app.callbacks import LessonPayCb, LessonCb, AdminCb


# ----------------- fakes -----------------
class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))


class FakeCallbackQuery:
    def __init__(self, user_id: int):
        self.from_user = FakeFromUser(user_id)
        self.message = FakeMessage()
        self.answer_calls = 0

    async def answer(self, *args, **kwargs):
        self.answer_calls += 1


def all_callback_data(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


# ----------------- tests: render_lesson_card -----------------
@pytest.mark.asyncio
async def test_render_lesson_card_shows_planned_without_pay_button(session, monkeypatch):
    import app.handlers.admin.lessons as lesson_mod

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    lesson = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=None,
    )
    session.add(lesson)
    await session.commit()

    call = FakeCallbackQuery(user_id=999)

    await lesson_mod.render_lesson_card(call, session, student_id=st_id, offset=0)

    assert call.message.edits
    text, kwargs = call.message.edits[-1]
    assert "Статус:" in text
    assert "planned" in text

    markup = kwargs["reply_markup"]
    cbs = all_callback_data(markup)

    # есть кнопка "Проведён"
    assert LessonCb(action="done", lesson_id=lesson.id, student_id=st_id, offset=0).pack() in cbs

    # нет кнопки "Урок оплачен"
    assert not any((cb or "").startswith("c:") for cb in cbs)


async def test_render_lesson_card_shows_done_pending_with_pay_button_and_hides_done(session):
    import app.handlers.admin.lessons as lesson_mod

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    lesson = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.done,
        source_rule_id=None,
    )
    session.add(lesson)
    await session.flush()

    ch = LessonCharge(
        lesson_id=lesson.id,
        student_id=st_id,
        amount=1000.0,
        status=ChargeStatus.pending,
    )
    session.add(ch)
    await session.commit()

    call = FakeCallbackQuery(user_id=999)
    await lesson_mod.render_lesson_card(call, session, student_id=st_id, offset=0)

    text, kwargs = call.message.edits[-1]
    assert "не оплачено" in text.lower()

    markup = kwargs["reply_markup"]
    cbs = all_callback_data(markup)

    # есть кнопка оплаты (теперь по lesson_id)
    assert LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0).pack() in cbs

    # "Проведён" скрыт (show_done=False для done-урока)
    assert LessonCb(action="done", lesson_id=lesson.id, student_id=st_id, offset=0).pack() not in cbs


@pytest.mark.asyncio
async def test_render_lesson_card_excludes_done_paid_and_canceled(session):
    import app.handlers.admin.lessons as lesson_mod

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    # done + PAID (по времени раньше planned) -> должен быть исключён
    lesson_done_paid = Lesson(
        student_id=st_id, start_at=t0, duration_min=60, status=LessonStatus.done, source_rule_id=None
    )
    session.add(lesson_done_paid)
    await session.flush()
    session.add(LessonCharge(
        lesson_id=lesson_done_paid.id, student_id=st_id, amount=1000.0, status=ChargeStatus.paid
    ))

    # canceled -> исключён
    lesson_canceled = Lesson(
        student_id=st_id, start_at=t0 + timedelta(hours=1), duration_min=60, status=LessonStatus.canceled, source_rule_id=None
    )
    session.add(lesson_canceled)

    # planned -> должен показаться
    lesson_planned = Lesson(
        student_id=st_id, start_at=t0 + timedelta(hours=2), duration_min=60, status=LessonStatus.planned, source_rule_id=None
    )
    session.add(lesson_planned)

    await session.commit()

    call = FakeCallbackQuery(user_id=999)
    await lesson_mod.render_lesson_card(call, session, student_id=st_id, offset=0)

    text, kwargs = call.message.edits[-1]
    # должен быть показан planned урок, а не done+paid и не canceled
    assert "planned" in text
    cbs = all_callback_data(kwargs["reply_markup"])
    assert LessonCb(action="done", lesson_id=lesson_planned.id, student_id=st_id, offset=0).pack() in cbs


# ----------------- tests: admin_student_card (pending counter) -----------------
@pytest.mark.asyncio
async def test_admin_student_card_shows_unpaid_count_for_single(session):
    import app.handlers.admin.students as mod

    teacher = User(tg_id=7000, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(teacher)
    await session.flush()

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    l1 = Lesson(student_id=st_id, start_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc), duration_min=60, status=LessonStatus.done, source_rule_id=None)
    l2 = Lesson(student_id=st_id, start_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc), duration_min=60, status=LessonStatus.done, source_rule_id=None)
    l3 = Lesson(student_id=st_id, start_at=datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc), duration_min=60, status=LessonStatus.done, source_rule_id=None)
    session.add_all([l1, l2, l3])
    await session.flush()

    session.add_all([
        LessonCharge(lesson_id=l1.id, student_id=st_id, amount=1000.0, status=ChargeStatus.pending),
        LessonCharge(lesson_id=l2.id, student_id=st_id, amount=1000.0, status=ChargeStatus.pending),
        LessonCharge(lesson_id=l3.id, student_id=st_id, amount=1000.0, status=ChargeStatus.paid),
    ])
    await session.commit()

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = AdminCb(action="student", student_id=st_id, page=1)

    await mod.admin_student_card(call, cb, session)

    text, _ = call.message.edits[-1]
    assert "Проведено, но не оплачено: 2" in text


@pytest.mark.asyncio
async def test_admin_student_card_shows_balance_for_subscription(session):
    import app.handlers.admin.students as mod

    teacher = User(tg_id=7001, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(teacher)
    await session.flush()

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()
    st_id = st.id

    session.add(StudentBalance(student_id=st_id, lessons_left=6))
    await session.commit()

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = AdminCb(action="student", student_id=st_id, page=1)

    await mod.admin_student_card(call, cb, session)

    text, _ = call.message.edits[-1]
    assert "Осталось уроков: 6" in text


@pytest.mark.asyncio
async def test_lesson_pay_paid_marks_paid_and_lesson_disappears(session):
    import app.handlers.admin.lessons as lesson_mod
    import app.handlers.admin.payments as payment_mod

    # teacher (для ensure_teacher в lesson_pay_action)
    teacher = User(tg_id=7100, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(teacher)
    await session.flush()

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.flush()
    st_id = st.id

    lesson = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.done,
        source_rule_id=None,
    )
    session.add(lesson)
    await session.flush()

    ch = LessonCharge(lesson_id=lesson.id, student_id=st_id, amount=1000.0, status=ChargeStatus.pending)
    session.add(ch)
    await session.commit()

    # убедимся: карточка урока сейчас показывает "не оплачено" и кнопку оплаты
    call0 = FakeCallbackQuery(user_id=teacher.tg_id)
    await lesson_mod.render_lesson_card(call0, session, student_id=st_id, offset=0)
    text0, kwargs0 = call0.message.edits[-1]
    assert "не оплачено" in text0.lower()
    assert LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0).pack() in all_callback_data(kwargs0["reply_markup"])

    # нажимаем "урок оплачен" (по lesson_id)
    call1 = FakeCallbackQuery(user_id=teacher.tg_id)
    await payment_mod.lesson_pay_action(call1, LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0), session)

    # начисление стало paid
    ch_db = (await session.execute(select(LessonCharge).where(LessonCharge.lesson_id == lesson.id))).scalar_one()
    assert ch_db.status == ChargeStatus.paid
    assert ch_db.paid_at is not None

    # теперь render_lesson_card должен сказать "Ближайших уроков нет."
    call2 = FakeCallbackQuery(user_id=teacher.tg_id)
    await lesson_mod.render_lesson_card(call2, session, student_id=st_id, offset=0)
    text2, _ = call2.message.edits[-1]
    assert "Ближайших уроков нет" in text2

