from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, update, delete, or_, and_, exists

from ..models import (
    User, Role, Lesson, LessonStatus, Student, ScheduleRule,
    Homework, ParentStudent, Parent, Notification, NotificationStatus,
    LessonCharge, ChargeStatus
)
from ..callbacks import LessonCb, AdminCb, ChargeCb, HomeworkCb
from ..keyboards import lesson_actions_kb, student_card_kb, charge_paid_kb, homework_kb
from ..services.billing import mark_lesson_done, mark_charge_paid
from ..utils_time import fmt_dt_for_tz

router = Router()

class HomeworkFSM(StatesGroup):
    title = State()
    description = State()
    grade = State()

def ensure_teacher(user: User):
    if user.role != Role.teacher:
        raise PermissionError


async def render_lesson_card(call: CallbackQuery, session, student_id: int, offset: int):
    # done-уроки показываем только если есть pending начисление
    unpaid_exists = exists(
        select(1).where(
            LessonCharge.lesson_id == Lesson.id,
            LessonCharge.status == ChargeStatus.pending
        )
    )

    lessons = (await session.execute(
        select(Lesson)
        .where(
            Lesson.student_id == student_id,
            or_(
                Lesson.status == LessonStatus.planned,
                and_(Lesson.status == LessonStatus.done, unpaid_exists),
            )
        )
        .order_by(Lesson.start_at)
        .offset(offset)
        .limit(1)
    )).scalars().all()

    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    if not lessons:
        await call.message.edit_text("Ближайших уроков нет.", reply_markup=student_card_kb(student_id))
        return

    lesson = lessons[0]
    when = fmt_dt_for_tz(lesson.start_at, st.timezone)
    is_recurring = lesson.source_rule_id is not None

    # если урок done и он попал в выборку, значит он "не оплачен" -> найдём charge_id
    charge_id = None
    status_text = "planned"
    if lesson.status == LessonStatus.done:
        status_text = "done (не оплачено)"
        ch = (await session.execute(
            select(LessonCharge).where(
                LessonCharge.lesson_id == lesson.id,
                LessonCharge.status == ChargeStatus.pending
            )
        )).scalar_one_or_none()
        charge_id = ch.id if ch else None

    text = (
        f"{st.full_name}\n"
        f"Урок: {when} ({st.timezone})\n"
        f"Тип: {'еженедельное' if is_recurring else 'разовое'}\n"
        f"Статус: {status_text}"
    )

    await call.message.edit_text(
        text,
        reply_markup=lesson_actions_kb(
            lesson.id, student_id, offset,
            is_recurring=is_recurring,
            charge_id=charge_id,
            show_done=(lesson.status == LessonStatus.planned),
        )
    )


@router.callback_query(AdminCb.filter(F.action == "lessons"))
async def admin_lessons(call: CallbackQuery, callback_data: AdminCb, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    if not callback_data.student_id:
        await call.answer("Не выбран ученик", show_alert=True)
        return

    await render_lesson_card(call, session, callback_data.student_id, offset=0)
    await call.answer()


@router.callback_query(LessonCb.filter())
async def lesson_action(call: CallbackQuery, callback_data: LessonCb, session, bot):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    student_id = callback_data.student_id or 0
    offset = callback_data.offset

    if callback_data.action == "next":
        await render_lesson_card(call, session, student_id, offset=offset + 1)
        await call.answer()
        return

    if callback_data.action == "prev":
        await render_lesson_card(call, session, student_id, offset=max(0, offset - 1))
        await call.answer()
        return

    if callback_data.action == "cancel":
        lesson = (await session.execute(select(Lesson).where(Lesson.id == callback_data.lesson_id))).scalar_one()

        # Разовое: отмена = удалить из календаря
        if lesson.source_rule_id is None:
            await session.delete(lesson)
            await session.commit()
            await call.message.edit_text(
                "Разовое занятие отменено и удалено из календаря.",
                reply_markup=student_card_kb(student_id)
            )
            await call.answer()
            return

        # Еженедельное: отменяем только ближайшее занятие (НЕ удаляем, чтобы генератор не пересоздал)
        lesson.status = LessonStatus.canceled
        await session.commit()
        await call.message.edit_text(
            "Ближайшее занятие из еженедельного цикла отменено.",
            reply_markup=student_card_kb(student_id)
        )
        await call.answer()
        return

    if callback_data.action == "delete_series":
        lesson = (await session.execute(select(Lesson).where(Lesson.id == callback_data.lesson_id))).scalar_one()

        if lesson.source_rule_id is None:
            await call.answer("Это разовое занятие. Цикла нет.", show_alert=True)
            return

        rule_id = lesson.source_rule_id
        now = datetime.now(timezone.utc)

        # ВАЖНО: сначала удаляем будущие уроки, потом удаляем правило.
        # Иначе из-за FK ondelete="SET NULL" уроки потеряют source_rule_id и станут выглядеть как разовые.
        await session.execute(
            delete(Lesson).where(
                Lesson.source_rule_id == rule_id,
                Lesson.start_at >= now
            )
        )

        rule = (await session.execute(select(ScheduleRule).where(ScheduleRule.id == rule_id))).scalar_one()
        await session.delete(rule)

        await session.commit()
        await call.message.edit_text(
            "Еженедельный цикл удалён (правило и будущие занятия).",
            reply_markup=student_card_kb(student_id)
        )
        await call.answer()
        return

    if callback_data.action == "done":
        await mark_lesson_done(session, bot, callback_data.lesson_id)

        # перерисовываем карточку урока на том же offset
        # (если single — появится "Урок оплачен", если subscription — урок исчезнет)
        await render_lesson_card(call, session, student_id=student_id, offset=offset)

        await call.answer()
        return


@router.callback_query(ChargeCb.filter(F.action == "paid"))
async def charge_paid(call: CallbackQuery, callback_data: ChargeCb, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    # 1) узнаём ученика по начислению
    ch = (await session.execute(
        select(LessonCharge).where(LessonCharge.id == callback_data.charge_id)
    )).scalar_one()
    student_id = ch.student_id

    # 2) отмечаем оплату
    await mark_charge_paid(session, callback_data.charge_id)

    # 3) перерисовываем карточку урока
    # после оплаты текущий урок выпадет из списка (done+paid), покажется следующий
    await render_lesson_card(call, session, student_id=student_id, offset=0)

    await call.answer()

async def render_homework(call: CallbackQuery, session, lesson_id: int, student_id: int, offset: int):
    lesson = (await session.execute(select(Lesson).where(Lesson.id == lesson_id))).scalar_one()
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    hw = (await session.execute(select(Homework).where(Homework.lesson_id == lesson_id))).scalar_one_or_none()

    when = fmt_dt_for_tz(lesson.start_at, st.timezone)
    text = [
        f"{st.full_name}",
        f"Урок: {when} ({st.timezone})",
        "",
        "Домашнее задание:",
    ]
    if not hw:
        text.append("— ещё не задано —")
    else:
        text.append(f"Название: {hw.title}")
        text.append(f"Описание: {hw.description}")
        text.append(f"Оценка: {hw.grade if hw.grade is not None else '-'} / 10")

    await call.message.edit_text("\n".join(text), reply_markup=homework_kb(lesson_id, student_id, offset))

@router.callback_query(HomeworkCb.filter())
async def homework_menu(call: CallbackQuery, callback_data: HomeworkCb, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == call.from_user.id))).scalar_one()
    ensure_teacher(user)

    lesson_id = callback_data.lesson_id
    student_id = callback_data.student_id
    offset = callback_data.offset

    if callback_data.action == "back":
        await render_lesson_card(call, session, student_id=student_id, offset=offset)
        await call.answer()
        return

    if callback_data.action == "view":
        await render_homework(call, session, lesson_id, student_id, offset)
        await call.answer()
        return

    if callback_data.action == "edit":
        await state.clear()
        await state.update_data(lesson_id=lesson_id, student_id=student_id, offset=offset)
        await state.set_state(HomeworkFSM.title)
        await call.message.edit_text("Введите название домашнего задания:")
        await call.answer()
        return

    if callback_data.action == "grade":
        await state.clear()
        await state.update_data(lesson_id=lesson_id, student_id=student_id, offset=offset)
        await state.set_state(HomeworkFSM.grade)
        await call.message.edit_text("Введите оценку за ДЗ (1–10):")
        await call.answer()
        return

@router.message(HomeworkFSM.title)
async def hw_set_title(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)

    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Повторите.")
        return

    await state.update_data(title=title)
    await state.set_state(HomeworkFSM.description)
    await message.answer("Введите описание домашнего задания:")


@router.message(HomeworkFSM.description)
async def hw_set_description(message, state: FSMContext, session):
    user = (await session.execute(select(User).where(User.tg_id == message.from_user.id))).scalar_one()
    ensure_teacher(user)

    desc = (message.text or "").strip()
    if len(desc) < 2:
        await message.answer("Описание слишком короткое. Повторите.")
        return

    data = await state.get_data()
    lesson_id = data["lesson_id"]
    student_id = data["student_id"]
    offset = data.get("offset", 0)
    title = data["title"]

    hw = (await session.execute(select(Homework).where(Homework.lesson_id == lesson_id))).scalar_one_or_none()
    if not hw:
        hw = Homework(lesson_id=lesson_id, title=title, description=desc, grade=None, graded_at=None)
        session.add(hw)
    else:
        hw.title = title
        hw.description = desc

    await session.commit()
    await state.clear()

    await message.answer("Домашнее задание сохранено.")
    # покажем экран ДЗ отдельным сообщением (проще, чем пытаться edit_text)
    fake_call = None  # не нужно
    # лучше просто дать кнопку "Открыть ДЗ" не делая костылей:
    # (если хотите, я сделаю хранение message_id и edit)
    await message.answer(
        "Открыть ДЗ для этого урока можно через карточку урока → «Домашнее задание»."
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
    lesson_id = data["lesson_id"]
    student_id = data["student_id"]

    hw = (await session.execute(select(Homework).where(Homework.lesson_id == lesson_id))).scalar_one_or_none()
    if not hw:
        await message.answer("Сначала задайте домашнее задание (название/описание), потом ставьте оценку.")
        return

    hw.grade = grade
    hw.graded_at = datetime.now(timezone.utc)

    lesson = (await session.execute(select(Lesson).where(Lesson.id == lesson_id))).scalar_one()
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    # соберём user_ids: ученик + родители
    target_user_ids: set[int] = set()
    if st.user_id:
        target_user_ids.add(st.user_id)

    parent_ids = (await session.execute(
        select(ParentStudent.parent_id).where(ParentStudent.student_id == student_id)
    )).scalars().all()

    if parent_ids:
        parent_user_ids = (await session.execute(
            select(Parent.user_id).where(Parent.id.in_(parent_ids))
        )).scalars().all()
        target_user_ids.update(parent_user_ids)

    users = []
    if target_user_ids:
        users = (await session.execute(select(User).where(User.id.in_(list(target_user_ids))))).scalars().all()

    now = datetime.now(timezone.utc)
    rows = []
    for u in users:
        tz = u.timezone or st.timezone or "Europe/Moscow"
        when = fmt_dt_for_tz(lesson.start_at, tz)
        payload = (
            "Оценка за домашнее задание выставлена.\n"
            f"Ученик: {st.full_name}\n"
            f"Урок: {when} ({tz})\n"
            f"ДЗ: {hw.title}\n"
            f"Оценка: {grade}/10"
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
        stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "type", "entity_id", "send_at"])
        await session.execute(stmt)

    await session.commit()
    await state.clear()

    await message.answer("Оценка сохранена. Уведомления поставлены в очередь отправки.")
