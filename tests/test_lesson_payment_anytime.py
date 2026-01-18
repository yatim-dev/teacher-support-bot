import pytest
from datetime import datetime, timezone
from sqlalchemy import select

from app.models import (
    User, Role,
    Student, BillingMode,
    Lesson, LessonStatus,
    LessonCharge, ChargeStatus,
)

from app.callbacks import LessonPayCb

import app.handlers.admin.lessons as lessons_mod
import app.handlers.admin.payments as payments_mod

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


def all_callback_data(markup) -> list[str]:
    if markup is None:
        return []
    res: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                res.append(btn.callback_data)
    return res


async def create_teacher(session, tg_id: int = 9000) -> User:
    t = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(t)
    await session.flush()
    return t


@pytest.mark.asyncio
async def test_render_planned_single_shows_pay_button(session):
    teacher = await create_teacher(session, tg_id=9101)

    st = Student(
        full_name="S",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=1500,
    )
    session.add(st)
    await session.flush()
    st_id = st.id

    lesson = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=None,
    )
    session.add(lesson)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await lessons_mod.render_lesson_card(call, session, student_id=st_id, offset=0)

    text, kwargs = msg.edits[-1]
    assert "оплата" in text.lower()
    assert "не оплачено" in text.lower()

    cds = all_callback_data(kwargs["reply_markup"])
    assert LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0).pack() in cds


@pytest.mark.asyncio
async def test_pay_lesson_anytime_creates_paid_charge_for_planned(session):
    teacher = await create_teacher(session, tg_id=9102)

    st = Student(
        full_name="S",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=2000,
    )
    session.add(st)
    await session.flush()
    st_id = st.id

    lesson = Lesson(
        student_id=st_id,
        start_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        duration_min=60,
        status=LessonStatus.planned,
        source_rule_id=None,
    )
    session.add(lesson)
    await session.commit()

    # Нажимаем "Урок оплачен" до проведения
    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await payments_mod.lesson_pay_action(
        call,
        LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0),
        session,
    )

    ch = (await session.execute(
        select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
    )).scalar_one()

    assert ch.student_id == st_id
    assert ch.status == ChargeStatus.paid
    assert ch.paid_at is not None
    # amount Numeric(10,2) может вернуться как Decimal — приводим к float для сравнения
    assert float(ch.amount) == 2000.0

    # При повторном рендере кнопки оплаты быть не должно
    msg2 = FakeMessage(FakeFromUser(teacher.tg_id))
    call2 = FakeCallbackQuery(from_user=msg2.from_user, message=msg2)
    await lessons_mod.render_lesson_card(call2, session, student_id=st_id, offset=0)

    text2, kwargs2 = msg2.edits[-1]
    assert "оплата" in text2.lower()
    assert "оплачено" in text2.lower()

    cds2 = all_callback_data(kwargs2["reply_markup"])
    assert LessonPayCb(action="paid", lesson_id=lesson.id, student_id=st_id, offset=0).pack() not in cds2


@pytest.mark.asyncio
async def test_done_paid_lesson_is_hidden_from_lesson_card(session):
    teacher = await create_teacher(session, tg_id=9103)

    st = Student(
        full_name="S",
        timezone="Europe/Moscow",
        billing_mode=BillingMode.single,
        price_per_lesson=1000,
    )
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

    # done + paid => должен исчезнуть из выборки render_lesson_card
    ch = LessonCharge(
        lesson_id=lesson.id,
        student_id=st_id,
        amount=1000,
        status=ChargeStatus.paid,
        paid_at=datetime.now(timezone.utc),
    )
    session.add(ch)
    await session.commit()

    msg = FakeMessage(FakeFromUser(teacher.tg_id))
    call = FakeCallbackQuery(from_user=msg.from_user, message=msg)

    await lessons_mod.render_lesson_card(call, session, student_id=st_id, offset=0)

    text, _ = msg.edits[-1]
    assert "Ближайших уроков нет" in text
