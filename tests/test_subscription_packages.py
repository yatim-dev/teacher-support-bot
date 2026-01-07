import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import User, Role, Student, BillingMode, StudentBalance
from app.callbacks import SubCb


# ------- fakes --------
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
        self.answered = []
        self.answer_calls = 0

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answer_calls += 1
        self.answered.append((text, show_alert))


# ------- helpers --------
async def mk_teacher(session, tg_id: int = 9001) -> User:
    u = User(tg_id=tg_id, role=Role.teacher, name="T", timezone="Europe/Moscow")
    session.add(u)
    await session.commit()
    return u


async def mk_student(session, billing_mode: BillingMode) -> Student:
    st = Student(full_name="S", timezone="Europe/Moscow", billing_mode=billing_mode)
    session.add(st)
    await session.commit()
    return st


async def add_subscription_package(session, student_id: int, lessons: int) -> int:
    if lessons not in (8, 12):
        raise ValueError("Пакет может быть только 8 или 12 уроков")

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    if st.billing_mode != BillingMode.subscription:
        raise ValueError("Пополнение пакетом доступно только для subscription")

    ins = insert(StudentBalance).values(student_id=student_id, lessons_left=lessons)
    stmt = ins.on_conflict_do_update(
        index_elements=[StudentBalance.student_id],  # PK
        set_={
            # прибавляем к текущему значению то, что пришло в INSERT
            "lessons_left": StudentBalance.lessons_left + ins.excluded.lessons_left
        },
    )

    await session.execute(stmt)
    await session.commit()

    bal = (await session.execute(
        select(StudentBalance).where(StudentBalance.student_id == student_id)
    )).scalar_one()
    return bal.lessons_left


@pytest.mark.asyncio
async def test_add_subscription_package_rejects_non_subscription(session):
    from app.services.billing import add_subscription_package

    st = await mk_student(session, BillingMode.single)
    with pytest.raises(ValueError):
        await add_subscription_package(session, st.id, 8)


@pytest.mark.asyncio
async def test_add_subscription_package_rejects_wrong_qty(session):
    from app.services.billing import add_subscription_package

    st = await mk_student(session, BillingMode.subscription)
    with pytest.raises(ValueError):
        await add_subscription_package(session, st.id, 10)


# ------- tests: handler --------
@pytest.mark.asyncio
async def test_sub_add_handler_adds_and_shows_left(session):
    import app.handlers.admin.subscription as sub_mod

    teacher = await mk_teacher(session, tg_id=9100)
    st = await mk_student(session, BillingMode.subscription)
    st_id = st.id

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = SubCb(action="add", student_id=st_id, qty=8)

    await sub_mod.sub_add(call, cb, session)

    assert call.answer_calls == 1
    text, show_alert = call.answered[0]
    assert show_alert is True
    assert "Добавлено 8" in (text or "")
    assert "Осталось" in (text or "")

    bal = (await session.execute(select(StudentBalance).where(StudentBalance.student_id == st_id))).scalar_one()
    assert bal.lessons_left == 8


@pytest.mark.asyncio
async def test_sub_add_handler_rejects_when_student_not_subscription(session):
    import app.handlers.admin.subscription as sub_mod

    teacher = await mk_teacher(session, tg_id=9101)
    st = await mk_student(session, BillingMode.single)

    call = FakeCallbackQuery(user_id=teacher.tg_id)
    cb = SubCb(action="add", student_id=st.id, qty=8)

    await sub_mod.sub_add(call, cb, session)

    assert call.answer_calls == 1
    text, show_alert = call.answered[0]
    assert show_alert is True
    assert "subscription" in (text or "").lower()
