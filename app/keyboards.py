from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

from .callbacks import (
    MenuCb, AdminCb, LessonCb, ChargeCb,
    TzCb, ChildCb, FsmNavCb, HomeworkCb, SubCb
)

TZ_LIST = [
    "Europe/Moscow",
    "Europe/Kaliningrad",
    "Asia/Yekaterinburg",
    "Asia/Novosibirsk",
    "Asia/Irkutsk",
    "Asia/Vladivostok",
]


def main_menu(role: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    if role == "teacher":
        kb.button(text="Админка", callback_data=MenuCb(section="admin").pack())

    if role == "student":
        kb.button(text="Расписание", callback_data=MenuCb(section="student_schedule").pack())

    if role == "parent":
        kb.button(text="Дети", callback_data=MenuCb(section="parent_children").pack())

    kb.button(text="Часовой пояс", callback_data=MenuCb(section="tz").pack())
    kb.button(text="Помощь", callback_data=MenuCb(section="help").pack())
    kb.adjust(2)
    return kb.as_markup()


def tz_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tz in TZ_LIST:
        kb.button(text=tz, callback_data=TzCb(value=tz).pack())
    kb.adjust(1)
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Ученики", callback_data=AdminCb(action="students", page=1).pack())
    kb.button(text="Создать ученика", callback_data=AdminCb(action="create_student").pack())
    kb.adjust(1)
    return kb.as_markup()


def students_list_kb(rows: list[tuple[int, str]], page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for sid, name in rows:
        kb.button(text=name, callback_data=AdminCb(action="student", student_id=sid).pack())
    kb.button(text="◀", callback_data=AdminCb(action="students", page=max(1, page - 1)).pack())
    kb.button(text="▶", callback_data=AdminCb(action="students", page=page + 1).pack())
    kb.adjust(1, 2)
    return kb.as_markup()


def student_card_kb(student_id: int, *, show_subscription: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(text="Добавить занятие", callback_data=AdminCb(action="lessons_add", student_id=student_id).pack())

    if show_subscription:
        kb.button(text="Абонемент +8", callback_data=SubCb(action="add", student_id=student_id, qty=8).pack())
        kb.button(text="Абонемент +12", callback_data=SubCb(action="add", student_id=student_id, qty=12).pack())

    kb.button(text="Ключ для ученика", callback_data=AdminCb(action="keys_student", student_id=student_id).pack())
    kb.button(text="Ключ для родителя", callback_data=AdminCb(action="keys_parent", student_id=student_id).pack())
    kb.button(text="Ближайшие уроки", callback_data=AdminCb(action="lessons", student_id=student_id).pack())
    kb.button(text="Удалить ученика", callback_data=AdminCb(action="student_delete", student_id=student_id).pack())
    kb.button(text="Назад к списку", callback_data=AdminCb(action="students", page=1).pack())

    kb.adjust(1, 2, 1, 1, 1, 1, 1)
    return kb.as_markup()


def add_lesson_type_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Разовое", callback_data=AdminCb(action="add_single", student_id=student_id).pack())
    kb.button(text="Еженедельное", callback_data=AdminCb(action="add_rule", student_id=student_id).pack())
    kb.button(text="Назад", callback_data=AdminCb(action="student", student_id=student_id).pack())
    kb.adjust(2, 1)
    return kb.as_markup()


def lesson_actions_kb(lesson_id: int, student_id: int, offset: int, is_recurring: bool, *,
    charge_id: int | None = None,
    show_done: bool = True,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # Верхние действия
    if show_done:
        kb.button(
            text="Проведён",
            callback_data=LessonCb(action="done", lesson_id=lesson_id, student_id=student_id, offset=offset).pack()
        )

    kb.button(
        text="Отменить",
        callback_data=LessonCb(action="cancel", lesson_id=lesson_id, student_id=student_id, offset=offset).pack()
    )

    # Кнопка оплаты появляется только если есть pending charge
    if charge_id is not None:
        kb.button(
            text="Урок оплачен",
            callback_data=ChargeCb(action="paid", charge_id=charge_id).pack()
        )

    # Навигация
    kb.button(text="◀", callback_data=LessonCb(action="prev", lesson_id=lesson_id, student_id=student_id, offset=offset).pack())
    kb.button(text="▶", callback_data=LessonCb(action="next", lesson_id=lesson_id, student_id=student_id, offset=offset).pack())

    # Домашка
    kb.button(
        text="Домашнее задание",
        callback_data=HomeworkCb(action="view", lesson_id=lesson_id, student_id=student_id, offset=offset).pack()
    )

    if is_recurring:
        kb.button(
            text="Удалить цикл",
            callback_data=LessonCb(action="delete_series", lesson_id=lesson_id, student_id=student_id, offset=offset).pack()
        )

    kb.button(text="Назад", callback_data=AdminCb(action="student", student_id=student_id).pack())

    # Раскладка:
    # - если show_done и charge_id есть: получится 3 кнопки вверху -> делаем (2,1)
    # - иначе (2)
    if show_done and charge_id is not None:
        kb.adjust(2, 1, 2, 1, 1, 1)
    else:
        kb.adjust(2, 2, 1, 1, 1)

    return kb.as_markup()


def charge_paid_kb(charge_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отметить оплату", callback_data=ChargeCb(action="paid", charge_id=charge_id).pack())
    kb.adjust(1)
    return kb.as_markup()


def parent_children_kb(children: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for sid, name in children:
        kb.button(text=name, callback_data=ChildCb(student_id=sid).pack())
    kb.adjust(1)
    return kb.as_markup()

def fsm_nav_kb(flow: str, student_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅ Назад", callback_data=FsmNavCb(action="back", flow=flow, student_id=student_id).pack())
    # "Отмена" вернёт в карточку ученика (если student_id есть), иначе просто в админку
    if student_id:
        kb.button(text="✖ Отмена", callback_data=FsmNavCb(action="cancel", flow=flow, student_id=student_id).pack())
    else:
        kb.button(text="✖ Отмена", callback_data=FsmNavCb(action="cancel", flow=flow, student_id=None).pack())
    kb.adjust(2)
    return kb.as_markup()

def after_rule_added_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Ближайшие уроки", callback_data=AdminCb(action="lessons", student_id=student_id).pack())
    kb.button(text="Карточка ученика", callback_data=AdminCb(action="student", student_id=student_id).pack())
    kb.adjust(1)
    return kb.as_markup()

def after_single_added_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Ближайшие уроки", callback_data=AdminCb(action="lessons", student_id=student_id).pack())
    kb.button(text="Карточка ученика", callback_data=AdminCb(action="student", student_id=student_id).pack())
    kb.adjust(1)
    return kb.as_markup()

def student_delete_confirm_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=AdminCb(action="student_delete_confirm", student_id=student_id).pack())
    kb.button(text="⬅ Нет, назад", callback_data=AdminCb(action="student", student_id=student_id).pack())
    kb.adjust(1)
    return kb.as_markup()

def homework_kb(lesson_id: int, student_id: int, offset: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Задать/Изменить", callback_data=HomeworkCb(action="edit", lesson_id=lesson_id, student_id=student_id, offset=offset).pack())
    kb.button(text="✅ Поставить оценку", callback_data=HomeworkCb(action="grade", lesson_id=lesson_id, student_id=student_id, offset=offset).pack())
    kb.button(text="⬅ Назад к уроку", callback_data=HomeworkCb(action="back", lesson_id=lesson_id, student_id=student_id, offset=offset).pack())
    kb.adjust(1)
    return kb.as_markup()

def subscription_packages_kb(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Абонемент +8", callback_data=SubCb(action="add", student_id=student_id, qty=8).pack())
    kb.button(text="Абонемент +12", callback_data=SubCb(action="add", student_id=student_id, qty=12).pack())
    kb.adjust(2)
    return kb.as_markup()