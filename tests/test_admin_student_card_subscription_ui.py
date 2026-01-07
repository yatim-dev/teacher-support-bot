import pytest
from sqlalchemy import select

from app.models import User, Role, Student, BillingMode, StudentBalance
from app.callbacks import AdminCb, SubCb


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


async def mk_teacher(session, tg_id: int = 9200) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


@pytest.mark.asyncio
async def test_admin_student_card_shows_balance_and_buttons_for_subscription(session):
    # поправьте import, если функция лежит не в students.py
    import app.handlers.admin.students as students_mod

    teacher = await mk_teacher(session, tg_id=9201)

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.subscription)
    session.add(st)
    await session.flush()

    session.add(StudentBalance(student_id=st.id, lessons_left=5))
    await session.commit()
    st_id = st.id

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = AdminCb(action="student", student_id=st_id, page=1)

    await students_mod.admin_student_card(call, cb, session)

    assert call.answer_calls == 1
    assert call.message.edits

    text, kwargs = call.message.edits[-1]
    assert "Осталось уроков" in text
    assert "5" in text

    markup = kwargs.get("reply_markup")
    assert markup is not None

    # проверим что кнопки SubCb присутствуют
    all_cb = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert SubCb(action="add", student_id=st_id, qty=8).pack() in all_cb
    assert SubCb(action="add", student_id=st_id, qty=12).pack() in all_cb


@pytest.mark.asyncio
async def test_admin_student_card_hides_balance_and_buttons_for_single(session):
    import app.handlers.admin.students as students_mod

    teacher = await mk_teacher(session, tg_id=9202)

    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=BillingMode.single, price_per_lesson=1000)
    session.add(st)
    await session.commit()
    st_id = st.id

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = AdminCb(action="student", student_id=st_id, page=1)

    await students_mod.admin_student_card(call, cb, session)

    text, kwargs = call.message.edits[-1]
    assert "Осталось уроков" not in text  # для single не показываем

    markup = kwargs.get("reply_markup")
    all_cb = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert not any((c or "").startswith("sub:") for c in all_cb)
