from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, update, delete, or_, and_, exists

from ..config import settings
from ..models import (
    User, Role, Lesson, LessonStatus, Student, ScheduleRule,
    Homework, ParentStudent, Parent, Notification, NotificationStatus,
    LessonCharge, ChargeStatus, BillingMode
)
from ..callbacks import LessonCb, AdminCb, HomeworkCb, LessonPayCb
from ..keyboards import lesson_actions_kb, student_card_kb, homework_kb, student_homework_kb
from ..services.billing import mark_lesson_done, mark_charge_paid
from ..utils_time import fmt_dt_for_tz
from app.handlers.admin.common import get_user, ensure_teacher  # как у тебя
router = Router()

class HomeworkFSM(StatesGroup):
    title = State()
    description = State()
    grade = State()

async def render_lesson_card(call: CallbackQuery, session, student_id: int, offset: int):
    st = (await session.execute(select(Student).where(Student.id == student_id))).scalar_one()

    # done-уроки показываем только если есть pending (проведён, но не оплачен)
    unpaid_done_exists = exists(
        select(1).where(
            LessonCharge.lesson_id == Lesson.id,
            LessonCharge.status == ChargeStatus.pending
        )
    )

    lessons = (await session.execute(
        select(Lesson)
        .where(
            Lesson.student_id == student_id,
            Lesson.status != LessonStatus.canceled,
            or_(
                Lesson.status == LessonStatus.planned,
                and_(Lesson.status == LessonStatus.done, unpaid_done_exists),
            )
        )
        .order_by(Lesson.start_at)
        .offset(offset)
        .limit(1)
    )).scalars().all()

    if not lessons:
        await call.message.edit_text("Ближайших уроков нет.", reply_markup=student_card_kb(student_id))
        return

    lesson = lessons[0]
    when = fmt_dt_for_tz(lesson.start_at, st.timezone)
    is_recurring = lesson.source_rule_id is not None

    # Оплата (важно для single): может быть отмечена заранее, до проведения
    pay_line = ""
    paid = False
    if st.billing_mode == BillingMode.single:
        ch = (await session.execute(
            select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
        )).scalar_one_or_none()

        if ch and ch.status == ChargeStatus.paid:
            paid = True
            pay_line = "Оплата: оплачено\n"
        else:
            pay_line = "Оплата: не оплачено\n"

    status_text = "planned" if lesson.status == LessonStatus.planned else "done"

    text = (
        f"{st.full_name}\n"
        f"Урок: {when} ({st.timezone})\n"
        f"Тип: {'еженедельное' if is_recurring else 'разовое'}\n"
        f"Статус: {status_text}\n"
        f"{pay_line}"
    ).rstrip()

    await call.message.edit_text(
        text,
        reply_markup=lesson_actions_kb(
            lesson.id, student_id, offset,
            is_recurring=is_recurring,
            show_done=(lesson.status == LessonStatus.planned),
            show_pay=(st.billing_mode == BillingMode.single and not paid),
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


async def render_homework(call: CallbackQuery, session, lesson_id: int, student_id: int, offset: int, *, for_student: bool = False):
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

        # ... внутри render_homework, в ветке if hw:
        if hw.student_done_at:
            done_when = fmt_dt_for_tz(hw.student_done_at, st.timezone)
            text.append("")
            text.append(f"Статус: выполнено (отмечено {done_when} ({st.timezone}))")

    if for_student:
        markup = student_homework_kb(lesson_id=lesson_id, student_id=student_id)
    else:
        markup = homework_kb(lesson_id, student_id, offset)

    await call.message.edit_text("\n".join(text), reply_markup=markup)

@router.callback_query(HomeworkCb.filter())
async def homework_menu(call: CallbackQuery, callback_data: HomeworkCb, state: FSMContext, session):
    user = (await session.execute(
        select(User).where(User.tg_id == call.from_user.id)
    )).scalar_one()

    lesson_id = callback_data.lesson_id
    offset = callback_data.offset

    # ==========================
    # РЕЖИМ УЧЕНИКА
    # ==========================
    if user.role == Role.student:
        if callback_data.action not in {"view", "done"}:
            await call.answer("Недоступно", show_alert=True)
            return

        st = (await session.execute(
            select(Student).where(Student.user_id == user.id)
        )).scalar_one()

        lesson = (await session.execute(
            select(Lesson).where(Lesson.id == lesson_id)
        )).scalar_one()

        # ученик может работать только со своим уроком
        if lesson.student_id != st.id:
            await call.answer("Недоступно", show_alert=True)
            return

        # --- view ---
        if callback_data.action == "view":
            await render_homework(
                call, session,
                lesson_id=lesson_id, student_id=st.id, offset=0,
                for_student=True
            )
            await call.answer()
            return

        # --- done ---
        hw = (await session.execute(
            select(Homework).where(Homework.lesson_id == lesson_id)
        )).scalar_one_or_none()

        if not hw:
            await call.answer("ДЗ ещё не задано", show_alert=True)
            return

        first_time = hw.student_done_at is None
        if first_time:
            hw.student_done_at = datetime.now(timezone.utc)
            await session.commit()

            # уведомление учителю (единственный учитель из settings)
            lesson_when = fmt_dt_for_tz(lesson.start_at, st.timezone)
            done_when = fmt_dt_for_tz(hw.student_done_at, st.timezone)

            notify_text = (
                "Ученик отметил ДЗ как выполненное\n\n"
                f"Ученик: {st.full_name}\n"
                f"Урок: {lesson_when} ({st.timezone})\n"
                f"Отметил: {done_when} ({st.timezone})\n"
                f"ДЗ: {hw.title or '-'}"
            )

            try:
                await call.bot.send_message(settings.teacher_tg_id, notify_text)
            except TelegramForbiddenError:
                # учитель ещё не запускал бота (Telegram запрещает писать первым)
                pass

        await render_homework(
            call, session,
            lesson_id=lesson_id, student_id=st.id, offset=0,
            for_student=True
        )
        await call.answer("Отмечено." if first_time else "Уже отмечено.")
        return

    # ==========================
    # РЕЖИМ УЧИТЕЛЯ
    # ==========================
    ensure_teacher(user)

    student_id = callback_data.student_id

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

    await call.answer("Неизвестное действие", show_alert=True)

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

@router.callback_query(LessonPayCb.filter(F.action == "paid"))
async def lesson_pay_action(call: CallbackQuery, callback_data: LessonPayCb, session):
    user = await get_user(session, call.from_user.id)
    ensure_teacher(user)

    lesson = (await session.execute(
        select(Lesson).where(Lesson.id == callback_data.lesson_id)
    )).scalar_one()

    st = (await session.execute(
        select(Student).where(Student.id == lesson.student_id)
    )).scalar_one()

    # абонемент — платить нечего
    if st.billing_mode != BillingMode.single:
        await call.answer("Для абонемента оплата не требуется", show_alert=True)
        return

    ch = (await session.execute(
        select(LessonCharge).where(LessonCharge.lesson_id == lesson.id)
    )).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if ch is None:
        ch = LessonCharge(
            lesson_id=lesson.id,
            student_id=st.id,
            amount=float(st.price_per_lesson or 0),
            status=ChargeStatus.paid,
            paid_at=now,
        )
        session.add(ch)
    else:
        ch.status = ChargeStatus.paid
        ch.paid_at = now

    await session.commit()

    # перерисовать карточку
    await render_lesson_card(call, session, student_id=callback_data.student_id, offset=callback_data.offset)
    await call.answer()