from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.sql import nulls_last
from sqlalchemy.dialects.postgresql import insert

from ...config import settings
from ...models import (
    User, Role, Student, Homework,
    ParentStudent, Parent, Notification, NotificationStatus
)
from ...callbacks import AdminCb, HomeworkCb, FsmNavCb
from ...keyboards import homework_kb, student_homework_kb, student_homeworks_list_kb, fsm_nav_kb, after_hw_added_kb
from ...utils_time import fmt_dt_for_tz
from .common import ensure_teacher
from ..student import render_student_card

router = Router()

class HomeworkFSM(StatesGroup):
    title = State()
    description = State()
    due_at = State()
    grade = State()

async def render_homework(
    call: CallbackQuery,
    session,
    homework_id: int,
    student_id: int,
    offset: int,
    *,
    for_student: bool = False,
):
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    hw = (
        await session.execute(
            select(Homework).where(Homework.id == homework_id)
        )
    ).scalar_one_or_none()

    if not hw:
        await call.message.edit_text("ДЗ не найдено.")
        return

    # защита от подмены student_id в callback
    if hw.student_id != st.id:
        await call.message.edit_text("Недоступно.")
        return

    tz = st.timezone or "Europe/Moscow"

    text = [
        f"{st.full_name}",
        "",
        "Домашнее задание:",
        f"Название: {hw.title}",
        f"Описание: {hw.description}",
        f"Оценка: {hw.grade if hw.grade is not None else '-'} / 10",
    ]

    if hw.due_at:
        due_when = fmt_dt_for_tz(hw.due_at, tz)
        text.insert(2, f"Сдать до: {due_when} ({tz})")  # после имени

    if hw.student_done_at:
        done_when = fmt_dt_for_tz(hw.student_done_at, tz)
        text.append("")
        text.append(f"Статус: выполнено (отмечено {done_when} ({tz}))")

    if for_student:
        markup = student_homework_kb(homework_id=homework_id, student_id=student_id)
    else:
        markup = homework_kb(homework_id=homework_id, student_id=student_id, offset=offset)

    await call.message.edit_text("\n".join(text), reply_markup=markup)


async def render_student_homeworks(call, session, student_id: int):
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    q = (
        select(Homework)
        .where(Homework.student_id == student_id)
        .order_by(nulls_last(Homework.due_at.desc()), Homework.created_at.desc())
    )
    homeworks = (await session.execute(q)).scalars().all()

    tz = st.timezone or "Europe/Moscow"

    lines = [f"{st.full_name}", "", "Домашние задания:"]
    if not homeworks:
        lines.append("— пока нет —")
    else:
        for hw in homeworks[:30]:
            parts = [f"• {hw.title or 'ДЗ'}"]
            if hw.due_at:
                parts.append(f"до {fmt_dt_for_tz(hw.due_at, tz)}")
            if hw.student_done_at:
                parts.append("выполнено")
            lines.append(" ".join(parts))

    markup = student_homeworks_list_kb(student_id, homeworks)
    await call.message.edit_text("\n".join(lines), reply_markup=markup)


@router.callback_query(AdminCb.filter(F.action == "homeworks"))
async def admin_student_homeworks(call: CallbackQuery, callback_data: AdminCb, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    await render_student_homeworks(call, session, student_id=callback_data.student_id)
    await call.answer()


@router.callback_query(AdminCb.filter(F.action == "hw_create"))
async def admin_hw_create_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    await state.clear()
    await state.update_data(student_id=callback_data.student_id)
    await state.set_state(HomeworkFSM.title)

    await call.message.edit_text(
        "Введите название домашнего задания:",
        reply_markup=fsm_nav_kb("hw_create", callback_data.student_id),
    )
    await call.answer()


@router.callback_query(HomeworkCb.filter())
async def homework_menu(call: CallbackQuery, callback_data: HomeworkCb, state: FSMContext, session):
    user = (
        await session.execute(select(User).where(User.tg_id == call.from_user.id))
    ).scalar_one_or_none()

    if not user:
        await call.answer("Сначала зарегистрируйтесь через /start", show_alert=True)
        return

    homework_id = callback_data.homework_id
    cb_student_id = callback_data.student_id
    offset = callback_data.offset

    # ==========================
    # РЕЖИМ УЧЕНИКА
    # ==========================
    if user.role == Role.student:
        if callback_data.action not in {"view", "done"}:
            await call.answer("Недоступно", show_alert=True)
            return

        st = (
            await session.execute(select(Student).where(Student.user_id == user.id))
        ).scalar_one_or_none()

        if not st:
            await call.answer("Профиль ученика не найден", show_alert=True)
            return

        hw = (
            await session.execute(select(Homework).where(Homework.id == homework_id))
        ).scalar_one_or_none()

        if not hw:
            await call.answer("ДЗ не найдено", show_alert=True)
            return

        # защита от подмены student_id в callback + "только своё ДЗ"
        if cb_student_id != st.id or hw.student_id != st.id:
            await call.answer("Недоступно", show_alert=True)
            return

        # --- view ---
        if callback_data.action == "view":
            await render_homework(
                call,
                session,
                homework_id=homework_id,
                student_id=st.id,
                offset=0,
                for_student=True,
            )
            await call.answer()
            return

        # --- done ---
        first_time = hw.student_done_at is None
        if first_time:
            hw.student_done_at = datetime.now(timezone.utc)
            await session.commit()

            tz = st.timezone or "Europe/Moscow"
            notify_text = (
                "Ученик отметил ДЗ как выполненное\n\n"
                f"Ученик: {st.full_name}\n"
                f"ДЗ: {hw.title or '-'}\n"
            )
            # если хотите — можно добавить времена:
            # done_when = fmt_dt_for_tz(hw.student_done_at, tz)
            # notify_text += f"Отметил: {done_when} ({tz})\n"
            # if hw.due_at:
            #     due_when = fmt_dt_for_tz(hw.due_at, tz)
            #     notify_text += f"Сдать до: {due_when} ({tz})\n"

            try:
                await call.bot.send_message(chat_id=settings.teacher_tg_id, text=notify_text)
            except TelegramForbiddenError:
                pass

        await render_homework(
            call,
            session,
            homework_id=homework_id,
            student_id=st.id,
            offset=0,
            for_student=True,
        )
        await call.answer("Отмечено." if first_time else "Уже отмечено.")
        return

    # ==========================
    # РЕЖИМ УЧИТЕЛЯ
    # ==========================
    ensure_teacher(user)

    hw = (
        await session.execute(select(Homework).where(Homework.id == homework_id))
    ).scalar_one_or_none()

    if not hw:
        await call.answer("ДЗ не найдено", show_alert=True)
        return

    # защита от подмены student_id в callback
    if hw.student_id != cb_student_id:
        await call.answer("Недоступно", show_alert=True)
        return

    if callback_data.action == "back":
        await render_student_homeworks(call, session, student_id=cb_student_id)
        await call.answer()
        return

    if callback_data.action == "view":
        await render_homework(
            call,
            session,
            homework_id=homework_id,
            student_id=cb_student_id,
            offset=offset,
            for_student=False,
        )
        await call.answer()
        return

    if callback_data.action == "edit":
        await state.clear()
        await state.update_data(homework_id=homework_id, student_id=cb_student_id, offset=offset)
        await state.set_state(HomeworkFSM.title)
        await call.message.edit_text("Введите название домашнего задания:")
        await call.answer()
        return

    if callback_data.action == "grade":
        await state.clear()
        await state.update_data(homework_id=homework_id, student_id=cb_student_id, offset=offset)
        await state.set_state(HomeworkFSM.grade)
        await call.message.edit_text("Введите оценку за ДЗ (1–10):")
        await call.answer()
        return

    await call.answer("Неизвестное действие", show_alert=True)


@router.message(HomeworkFSM.title)
async def hw_set_title(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)
    data = await state.get_data()
    student_id = data.get("student_id")

    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Повторите.")
        return

    await state.update_data(title=title)
    await state.set_state(HomeworkFSM.description)
    await message.answer(
        "Введите описание домашнего задания:",
        reply_markup=fsm_nav_kb("hw_create", student_id)
    )


@router.message(HomeworkFSM.description)
async def hw_set_description(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)

    desc = (message.text or "").strip()
    if len(desc) < 2:
        await message.answer("Описание слишком короткое. Повторите.")
        return

    data = await state.get_data()
    student_id = data["student_id"]
    title = data["title"]

    hw = Homework(student_id=student_id, title=title, description=desc, grade=None, graded_at=None)
    session.add(hw)
    await session.commit()
    await session.refresh(hw)

    await state.update_data(homework_id=hw.id)
    await state.set_state(HomeworkFSM.due_at)

    await message.answer(
        "Введите дедлайн в формате `YYYY-MM-DD HH:MM`, например `2026-02-11 12:00` или \"-\" чтобы без дедлайна.",
        parse_mode="Markdown",
        reply_markup=fsm_nav_kb("hw_create", student_id)
    )


@router.message(HomeworkFSM.due_at)
async def hw_set_due_at(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)

    raw = (message.text or "").strip()

    data = await state.get_data()
    homework_id = data.get("homework_id")
    student_id = data["student_id"]

    if homework_id is None:
        await message.answer("Ошибка: не найдено ДЗ для установки дедлайна.")
        await state.clear()
        return

    hw = (await session.execute(select(Homework).where(Homework.id == homework_id))).scalar_one_or_none()
    if not hw:
        await message.answer("ДЗ не найдено.")
        await state.clear()
        return

    if hw.student_id != student_id:
        await message.answer("Ошибка: ДЗ не принадлежит выбранному ученику.")
        await state.clear()
        return

    # "-" означает "без дедлайна"
    if raw == "-":
        hw.due_at = None
        await session.commit()
        await state.clear()
        await message.answer("Дедлайн убран. Домашнее задание сохранено.")
        return

    # Парсим время как локальное время ученика/его TZ и конвертируем в UTC
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()
    tz_name = st.timezone or "Europe/Moscow"
    tz = ZoneInfo(tz_name)

    try:
        dt_local = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Неверный формат. Нужно `YYYY-MM-DD HH:MM` или `-`.")
        return

    dt_local = dt_local.replace(tzinfo=tz)          # локальная TZ ученика
    hw.due_at = dt_local.astimezone(timezone.utc)   # храним в UTC

    await session.commit()
    await state.clear()

    await message.answer(
        "Дедлайн сохранён. Домашнее задание сохранено.",
        reply_markup=after_hw_added_kb(student_id)
    )


@router.message(HomeworkFSM.grade)
async def hw_set_grade(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)

    try:
        grade = int((message.text or "").strip())
        if grade < 1 or grade > 10:
            raise ValueError
    except Exception:
        await message.answer("Нужна оценка целым числом 1–10.")
        return

    data = await state.get_data()
    homework_id = data.get("homework_id")
    student_id = data["student_id"]

    if homework_id is None:
        await message.answer("Ошибка: не найдено ДЗ для выставления оценки.")
        await state.clear()
        return

    hw = (await session.execute(select(Homework).where(Homework.id == homework_id))).scalar_one_or_none()
    if not hw:
        await message.answer("ДЗ не найдено.")
        await state.clear()
        return

    if hw.student_id != student_id:
        await message.answer("Ошибка: ДЗ не принадлежит выбранному ученику.")
        await state.clear()
        return

    hw.grade = grade
    hw.graded_at = datetime.now(timezone.utc)

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    # соберём user_ids: ученик + родители
    target_user_ids: set[int] = set()
    if st.user_id:
        target_user_ids.add(st.user_id)

    parent_ids = (
        await session.execute(
            select(ParentStudent.parent_id).where(ParentStudent.student_id == student_id)
        )
    ).scalars().all()

    if parent_ids:
        parent_user_ids = (
            await session.execute(
                select(Parent.user_id).where(Parent.id.in_(parent_ids))
            )
        ).scalars().all()
        target_user_ids.update(parent_user_ids)

    users = []
    if target_user_ids:
        users = (
            await session.execute(select(User).where(User.id.in_(list(target_user_ids))))
        ).scalars().all()

    now = datetime.now(timezone.utc)
    rows = []

    for u in users:
        tz = u.timezone or st.timezone or "Europe/Moscow"

        due_line = ""
        if hw.due_at:
            due_when = fmt_dt_for_tz(hw.due_at, tz)
            due_line = f"\nСдать до: {due_when} ({tz})"

        payload = (
            "Оценка за домашнее задание выставлена.\n"
            f"Ученик: {st.full_name}\n"
            f"ДЗ: {hw.title}\n"
            f"Оценка: {grade}/10"
            f"{due_line}"
        )

        rows.append({
            "user_id": u.id,
            "type": "hw_graded",
            "entity_id": hw.id,
            "send_at": now,
            "payload": payload,
            "status": NotificationStatus.pending
        })

    if rows:
        stmt = insert(Notification).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["user_id", "type", "entity_id", "send_at"]
        )
        await session.execute(stmt)

    await session.commit()
    await state.clear()

    await message.answer("Оценка сохранена. Уведомления поставлены в очередь отправки.")

@router.callback_query(FsmNavCb.filter(F.flow.in_({"hw_create", "hw_edit", "hw_grade"})))
async def hw_fsm_nav(call: CallbackQuery, callback_data: FsmNavCb, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    student_id = callback_data.student_id
    if not student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    # --- CANCEL -> карточка ученика ---
    if callback_data.action == "cancel":
        await state.clear()
        await render_student_card(call.message, session, student_id=student_id)
        await call.answer()
        return

    # --- BACK ---
    cur = await state.get_state()

    # на первом шаге back == cancel
    if cur in (None, HomeworkFSM.title.state):
        await state.clear()
        await render_student_card(call.message, session, student_id=student_id)
        await call.answer()
        return

    if cur == HomeworkFSM.description.state:
        await state.set_state(HomeworkFSM.title)
        text = "Введите название домашнего задания:"
    elif cur == HomeworkFSM.due_at.state:
        await state.set_state(HomeworkFSM.description)
        text = "Введите описание домашнего задания:"
    elif cur == HomeworkFSM.grade.state:
        # назад из оценки — логичнее в карточку ДЗ
        data = await state.get_data()
        homework_id = data.get("homework_id")
        offset = data.get("offset", 0)
        await state.clear()
        if homework_id:
            await render_homework(call, session, homework_id=homework_id, student_id=student_id, offset=offset, for_student=False)
        else:
            await render_student_card(call.message, session, student_id=student_id)
        await call.answer()
        return
    else:
        await state.clear()
        await render_student_card(call.message, session, student_id=student_id)
        await call.answer()
        return

    try:
        await call.message.edit_text(text, reply_markup=fsm_nav_kb(callback_data.flow, student_id))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await call.answer()
