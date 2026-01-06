import secrets
from datetime import datetime, timedelta, timezone, date, time as dtime
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, exists, and_
from sqlalchemy.exc import IntegrityError

from ..models import (
    User, Role, Student, BillingMode, ScheduleRule,
    RegistrationKey, Lesson, LessonStatus, Notification,
    ParentStudent, Parent
)
from ..callbacks import MenuCb, AdminCb, FsmNavCb
from ..keyboards import (
    admin_menu, students_list_kb, student_card_kb, TZ_LIST, add_lesson_type_kb, fsm_nav_kb,
    after_rule_added_kb, after_single_added_kb, student_delete_confirm_kb
)
from ..services.schedule import generate_lessons_for_student
from ..jobs_notifications import plan_lesson_notifications_job

router = Router()


# ---------- helpers ----------

async def get_user(session, tg_id: int) -> User | None:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def ensure_teacher(user: User | None):
    if not user or user.role != Role.teacher:
        raise PermissionError("Teacher only")


def local_to_utc(student_tz: str, d: date, t: dtime) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, t.hour, t.minute, 0, tzinfo=ZoneInfo(student_tz))
    return local_dt.astimezone(timezone.utc)


# ---------- FSM ----------

class CreateStudentFSM(StatesGroup):
    full_name = State()
    tz = State()
    billing = State()
    price = State()


class AddRuleFSM(StatesGroup):
    weekday = State()
    time_local = State()
    duration = State()
    start_date = State()


class AddSingleLessonFSM(StatesGroup):
    date_ = State()
    time_ = State()
    duration = State()


# ---------- Admin root ----------

@router.callback_query(MenuCb.filter(F.section == "admin"))
async def admin_root(call: CallbackQuery, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    text = (
        "Админка\n\n"
        "• Ученики — список учеников и управление конкретным учеником.\n"
        "• Создать ученика — добавьте нового ученика (ФИО, TZ, тариф).\n\n"
        "Подсказка: у ученика можно добавить разовое занятие или еженедельный цикл."
    )
    await call.message.edit_text(text, reply_markup=admin_menu())
    await call.answer()

# ---------- Students list / card ----------

@router.callback_query(AdminCb.filter(F.action == "students"))
async def admin_students(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    page = callback_data.page
    page_size = 10
    offset = (page - 1) * page_size

    rows = (await session.execute(
        select(Student.id, Student.full_name)
        .order_by(Student.full_name)
        .offset(offset)
        .limit(page_size)
    )).all()

    await call.message.edit_text("Ученики:", reply_markup=students_list_kb(rows, page))
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "student"))
async def admin_student_card(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    st = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()

    txt = (
        f"Ученик: {st.full_name}\n"
        f"TZ ученика: {st.timezone}\n"
        f"Тариф: {st.billing_mode.value}\n"
        f"Цена за урок (если single): {st.price_per_lesson or '-'}\n"
        f"Зарегистрирован: {'да' if st.user_id else 'нет'}\n\n"
        "Дальше:\n"
        "1) Добавить занятие (разовое или еженедельное)\n"
        "2) Сгенерировать ключи (ученик/родитель)\n"
        "3) Проверить ближайшие уроки"
    )
    await call.message.edit_text(txt, reply_markup=student_card_kb(st.id))
    await call.answer()

# ---------- Create student ----------

@router.callback_query(AdminCb.filter(F.action == "create_student"))
async def create_student_start(call: CallbackQuery, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    await state.set_state(CreateStudentFSM.full_name)
    await call.message.edit_text("Создание ученика.\n\nВведите ФИО ученика (сообщением):")
    await call.answer()


@router.message(CreateStudentFSM.full_name)
async def create_student_full_name(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    full_name = (message.text or "").strip()
    if len(full_name) < 2:
        await message.answer("ФИО слишком короткое. Повторите.")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(CreateStudentFSM.tz)
    await message.answer("Выберите TZ ученика (сообщением, строго из списка):\n" + "\n".join(TZ_LIST))


@router.message(CreateStudentFSM.tz)
async def create_student_tz(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    tz = (message.text or "").strip()
    if tz not in TZ_LIST:
        await message.answer("TZ не из списка. Повторите:\n" + "\n".join(TZ_LIST))
        return

    await state.update_data(tz=tz)
    await state.set_state(CreateStudentFSM.billing)
    await message.answer("Выберите тариф: subscription или single (сообщением).")


@router.message(CreateStudentFSM.billing)
async def create_student_billing(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    val = (message.text or "").strip()
    if val not in ("subscription", "single"):
        await message.answer("Нужно написать: subscription или single.")
        return

    await state.update_data(billing=val)

    if val == "single":
        await state.set_state(CreateStudentFSM.price)
        await message.answer("Введите цену за урок (например 1500):")
    else:
        await finalize_student(message, state, session)


@router.message(CreateStudentFSM.price)
async def create_student_price(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    try:
        price = float((message.text or "").replace(",", "."))
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("Введите число > 0, например 1500")
        return

    await state.update_data(price=price)
    await finalize_student(message, state, session)


async def finalize_student(message: Message, state: FSMContext, session):
    data = await state.get_data()

    billing_mode = BillingMode(data["billing"])
    price = data.get("price")

    st = Student(
        full_name=data["full_name"],
        timezone=data["tz"],
        billing_mode=billing_mode,
        price_per_lesson=price if billing_mode == BillingMode.single else None
    )
    session.add(st)
    await session.commit()
    await state.clear()

    await message.answer(
        f"Ученик создан: {st.full_name}\n"
        f"ID: {st.id}\n\n"
        f"Дальше: /menu → Админка → Ученики → выберите ученика."
    )


# ---------- Add lesson: choose type ----------

@router.callback_query(AdminCb.filter(F.action == "lesson_add"))
async def lesson_add_choose(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    if not callback_data.student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await call.message.edit_text(
        "Добавить занятие:\n\n"
        "• Разовое — создаст один урок на дату/время.\n"
        "• Еженедельное — создаст цикл по дню недели.",
        reply_markup=add_lesson_type_kb(callback_data.student_id)
    )
    await call.answer()

# ---------- Add single lesson (one-time) ----------

@router.message(AddSingleLessonFSM.duration)
async def add_single_duration(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    try:
        dur = int((message.text or "").strip())
        if dur <= 0 or dur > 600:
            raise ValueError
    except Exception:
        await message.answer(
            "Введите целое число 1..600",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    dval: date = data["date_"]
    tval: dtime = data["time_"]

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    start_at_utc = local_to_utc(st.timezone, dval, tval)

    lesson = Lesson(
        student_id=student_id,
        start_at=start_at_utc,
        duration_min=dur,
        status=LessonStatus.planned,
        source_rule_id=None
    )
    session.add(lesson)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # остаёмся в FSM (не очищаем), чтобы работала кнопка "Назад"
        await message.answer(
            "На это время уже есть занятие.\n"
            "Нажмите «Назад» и выберите другую дату/время.",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    # Планируем напоминания сразу (без ожидания)
    await plan_lesson_notifications_job()

    await state.clear()
    await message.answer(
        "Разовое занятие создано.\n"
        "Напоминания запланированы.\n\n"
        "Куда перейти?",
        reply_markup=after_single_added_kb(student_id)
    )

# ---------- Add weekly (schedule rule) ----------

@router.callback_query(AdminCb.filter(F.action == "add_rule"))
async def add_rule_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    await state.update_data(student_id=callback_data.student_id)
    await state.set_state(AddRuleFSM.weekday)
    await call.message.edit_text(
        "Еженедельное занятие.\n\n"
        "Введите день недели:\n"
        "1 = ПН\n2 = ВТ\n3 = СР\n4 = ЧТ\n5 = ПТ\n6 = СБ\n7 = ВС",
        reply_markup=fsm_nav_kb("add_rule", callback_data.student_id)
    )

    await call.answer()


@router.message(AddRuleFSM.weekday)
async def add_rule_weekday(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    try:
        wd_user = int((message.text or "").strip())
        if wd_user < 1 or wd_user > 7:
            raise ValueError
    except Exception:
        await message.answer(
            "Введите число 1..7: 1=ПН, 2=ВТ, 3=СР, 4=ЧТ, 5=ПТ, 6=СБ, 7=ВС",
            reply_markup=fsm_nav_kb("add_rule", student_id)
        )
        return

    wd_db = wd_user - 1  # Python weekday() 0..6
    await state.update_data(weekday=wd_db)
    await state.set_state(AddRuleFSM.time_local)

    await message.answer(
        "Введите время HH:MM (локальное время ученика), например 16:30",
        reply_markup=fsm_nav_kb("add_rule", student_id)
    )


@router.message(AddRuleFSM.time_local)
async def add_rule_time(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    txt = (message.text or "").strip()
    try:
        hh, mm = txt.split(":")
        t = dtime(hour=int(hh), minute=int(mm))
    except Exception:
        await message.answer("Формат HH:MM, например 16:30")
        return

    await state.update_data(time_local=t)
    await state.set_state(AddRuleFSM.duration)
    await message.answer("Введите длительность (мин), например 60")


@router.message(AddRuleFSM.duration)
async def add_rule_duration(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    try:
        dur = int((message.text or "").strip())
        if dur <= 0 or dur > 600:
            raise ValueError
    except Exception:
        await message.answer("Введите целое число 1..600")
        return

    await state.update_data(duration_min=dur)
    await state.set_state(AddRuleFSM.start_date)
    await message.answer("Введите дату начала YYYY-MM-DD, например 2026-01-10")


@router.message(AddRuleFSM.start_date)
async def add_rule_start_date(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    txt = (message.text or "").strip()
    try:
        y, m, d = map(int, txt.split("-"))
        sd = date(y, m, d)
    except Exception:
        await message.answer("Формат YYYY-MM-DD, например 2026-01-10")
        return

    data = await state.get_data()
    student_id = data["student_id"]

    rule = ScheduleRule(
        student_id=student_id,
        weekday=data["weekday"],
        time_local=data["time_local"],
        duration_min=data["duration_min"],
        start_date=sd,
        end_date=None,
        active=True
    )
    session.add(rule)
    await session.commit()

    # сразу генерируем уроки (без ожидания)
    _ = await generate_lessons_for_student(session, student_id)
    await session.commit()

    # сразу планируем напоминания (без ожидания)
    await plan_lesson_notifications_job()

    await state.clear()
    await message.answer(
        "Еженедельное правило добавлено.\n"
        "Уроки сгенерированы и напоминания запланированы.\n\n"
        "Куда перейти?",
        reply_markup=after_rule_added_kb(student_id)
    )

# ---------- Keys generation ----------

async def create_key(session, role_target: Role, student_id: int) -> str:
    key = secrets.token_urlsafe(10)
    rk = RegistrationKey(
        key=key,
        role_target=role_target,
        student_id=student_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        max_uses=1,
        used_count=0,
        active=True
    )
    session.add(rk)
    await session.commit()
    return key


@router.callback_query(AdminCb.filter(F.action.in_({"keys_student", "keys_parent"})))
async def admin_keys(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    st = (await session.execute(select(Student).where(Student.id == callback_data.student_id))).scalar_one()

    if callback_data.action == "keys_student":
        key = await create_key(session, Role.student, st.id)
        text = (
            f"Ключ для ученика ({st.full_name}):\n"
            f"`{key}`\n\n"
            f"Инструкция: ученик пишет боту /start и вводит этот ключ."
        )
    else:
        key = await create_key(session, Role.parent, st.id)
        text = (
            f"Ключ для родителя ({st.full_name}):\n"
            f"`{key}`\n\n"
            f"Инструкция: родитель пишет боту /start и вводит этот ключ."
        )

    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=student_card_kb(st.id))
    await call.answer()


@router.callback_query(FsmNavCb.filter(F.flow == "add_rule"))
async def fsm_add_rule_nav(call: CallbackQuery, callback_data: FsmNavCb, state: FSMContext):
    data = await state.get_data()
    student_id = callback_data.student_id or data.get("student_id")

    if callback_data.action == "cancel":
        await state.clear()
        if student_id:
            await call.message.edit_text("Отменено.", reply_markup=student_card_kb(student_id))
        else:
            await call.message.edit_text("Отменено.")
        await call.answer()
        return

    # back
    current = await state.get_state()

    # шаги назад: start_date -> duration -> time_local -> weekday -> выход
    if current == AddRuleFSM.start_date.state:
        await state.set_state(AddRuleFSM.duration)
        await call.message.edit_text("Введите длительность (мин), например 60", reply_markup=fsm_nav_kb("add_rule", student_id))
    elif current == AddRuleFSM.duration.state:
        await state.set_state(AddRuleFSM.time_local)
        await call.message.edit_text("Введите время HH:MM (локальное время ученика), например 16:30", reply_markup=fsm_nav_kb("add_rule", student_id))
    elif current == AddRuleFSM.time_local.state:
        await state.set_state(AddRuleFSM.weekday)
        await call.message.edit_text(
            "Введите день недели:\n1=ПН\n2=ВТ\n3=СР\n4=ЧТ\n5=ПТ\n6=СБ\n7=ВС",
            reply_markup=fsm_nav_kb("add_rule", student_id)
        )
    else:
        # если мы на первом шаге — вернёмся к выбору типа занятия
        await state.clear()
        await call.message.edit_text("Добавить занятие:", reply_markup=add_lesson_type_kb(student_id))
    await call.answer()


@router.callback_query(FsmNavCb.filter(F.flow == "add_single"))
async def fsm_add_single_nav(call: CallbackQuery, callback_data: FsmNavCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = callback_data.student_id or data.get("student_id")

    if callback_data.action == "cancel":
        await state.clear()
        await call.message.edit_text("Отменено.", reply_markup=student_card_kb(student_id))
        await call.answer()
        return

    current = await state.get_state()

    if current == AddSingleLessonFSM.duration.state:
        await state.set_state(AddSingleLessonFSM.time_)
        await call.message.edit_text(
            "Введите время HH:MM (локальное время ученика), например 16:30",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
    elif current == AddSingleLessonFSM.time_.state:
        await state.set_state(AddSingleLessonFSM.date_)
        await call.message.edit_text(
            "Введите дату YYYY-MM-DD, например 2026-01-10:",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
    else:
        # если уже на первом шаге
        await state.clear()
        await call.message.edit_text("Добавить занятие:", reply_markup=add_lesson_type_kb(student_id))

    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "add_single"))
async def add_single_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    if not student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await state.update_data(student_id=student_id)
    await state.set_state(AddSingleLessonFSM.date_)

    await call.message.edit_text(
        "Разовое занятие.\nВведите дату YYYY-MM-DD, например 2026-01-10:",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )
    await call.answer()


@router.message(AddSingleLessonFSM.date_)
async def add_single_date(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    txt = (message.text or "").strip()
    try:
        y, m, d = map(int, txt.split("-"))
        dval = date(y, m, d)
    except Exception:
        await message.answer(
            "Формат YYYY-MM-DD, например 2026-01-10",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    await state.update_data(date_=dval)
    await state.set_state(AddSingleLessonFSM.time_)

    await message.answer(
        "Введите время HH:MM (локальное время ученика), например 16:30",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )


@router.message(AddSingleLessonFSM.time_)
async def add_single_time(message: Message, state: FSMContext, session):
    user = await get_user(session, message.from_user.id)
    ensure_teacher(user)

    data = await state.get_data()
    student_id = data.get("student_id")

    txt = (message.text or "").strip()
    try:
        hh, mm = txt.split(":")
        tval = dtime(hour=int(hh), minute=int(mm))
    except Exception:
        await message.answer(
            "Формат HH:MM, например 16:30",
            reply_markup=fsm_nav_kb("add_single", student_id)
        )
        return

    await state.update_data(time_=tval)
    await state.set_state(AddSingleLessonFSM.duration)

    await message.answer(
        "Введите длительность (мин), например 60",
        reply_markup=fsm_nav_kb("add_single", student_id)
    )


@router.callback_query(AdminCb.filter(F.action == "student_delete"))
async def student_delete_ask(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    text = (
        "Удаление ученика\n\n"
        f"Ученик: {st.full_name}\n"
        "Будут удалены:\n"
        "• ученик и его пользователь (если зарегистрирован)\n"
        "• все уроки, правила, баланс, начисления\n"
        "• ключи регистрации\n"
        "• уведомления по его урокам\n\n"
        "Действие необратимо. Удалить?"
    )
    await call.message.edit_text(text, reply_markup=student_delete_confirm_kb(student_id))
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "student_delete_confirm"))
async def student_delete_confirm(call: CallbackQuery, callback_data: AdminCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    student_id = callback_data.student_id
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    student_user_id = st.user_id  # запомним до удаления

    # --- 1) Какие родители привязаны к ученику (и только к нему) ---
    # parent_ids, у которых НЕ существует другой связи parent_student с другим student_id
    ps2 = ParentStudent.__table__.alias("ps2")
    parent_ids_to_delete = (await session.execute(
        select(ParentStudent.parent_id)
        .where(ParentStudent.student_id == student_id)
        .where(
            ~exists(
                select(1).select_from(ps2).where(
                    and_(
                        ps2.c.parent_id == ParentStudent.parent_id,
                        ps2.c.student_id != student_id
                    )
                )
            )
        )
        .distinct()
    )).scalars().all()

    # user_id этих родителей (удалим users -> каскадом удалится Parent)
    parent_user_ids_to_delete: list[int] = []
    if parent_ids_to_delete:
        parent_user_ids_to_delete = (await session.execute(
            select(Parent.user_id).where(Parent.id.in_(parent_ids_to_delete))
        )).scalars().all()

    # --- 2) Удаляем уведомления по урокам ученика (иначе send_notifications_job будет падать) ---
    lesson_ids = (await session.execute(
        select(Lesson.id).where(Lesson.student_id == student_id)
    )).scalars().all()

    if lesson_ids:
        await session.execute(
            delete(Notification).where(
                Notification.type.in_(("lesson_24h", "lesson_1h")),
                Notification.entity_id.in_(lesson_ids),
            )
        )

    # --- 3) Удаляем регистрационные ключи ученика (чтобы не плодить orphan с student_id=NULL) ---
    await session.execute(delete(RegistrationKey).where(RegistrationKey.student_id == student_id))

    # --- 4) Удаляем самого ученика (CASCADE удалит lessons/schedule_rules/balance/charges/parent_student) ---
    await session.delete(st)

    # --- 5) Удаляем user ученика (если был) ---
    if student_user_id:
        await session.execute(delete(User).where(User.id == student_user_id))

    # --- 6) Удаляем users родителей, у которых больше нет детей ---
    if parent_user_ids_to_delete:
        await session.execute(delete(User).where(User.id.in_(parent_user_ids_to_delete)))

    await session.commit()

    await call.message.edit_text("Ученик и связанные данные удалены.", reply_markup=admin_menu())
    await call.answer()
