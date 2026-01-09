# tests/test_student_keyboards.py
from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup

from app.callbacks import HomeworkCb, MenuCb
from app.keyboards import student_schedule_homework_kb, student_homework_back_kb


def _lesson(i: int):
    return SimpleNamespace(id=i)


def test_student_schedule_homework_kb_back_is_last_row():
    lessons = [_lesson(i) for i in range(1, 9)]  # 8 уроков
    markup = student_schedule_homework_kb(student_id=10, lessons=lessons, per_row=6)

    assert isinstance(markup, InlineKeyboardMarkup)
    rows = markup.inline_keyboard

    # 6 ДЗ, 2 ДЗ, 1 Назад
    assert [len(r) for r in rows] == [6, 2, 1]

    back_btn = rows[-1][0]
    assert back_btn.text == "Назад"

    back = MenuCb.unpack(back_btn.callback_data)
    assert back.section == "menu"

    dz_buttons = [b for row in rows[:-1] for b in row]
    assert all(b.text == "ДЗ" for b in dz_buttons)

    first = HomeworkCb.unpack(dz_buttons[0].callback_data)
    assert first.action == "view"
    assert first.student_id == 10
    assert first.offset == 0


def test_student_schedule_homework_kb_zero_lessons_only_back():
    markup = student_schedule_homework_kb(student_id=10, lessons=[], per_row=6)
    rows = markup.inline_keyboard

    assert [len(r) for r in rows] == [1]
    assert rows[0][0].text == "Назад"

    back = MenuCb.unpack(rows[0][0].callback_data)
    assert back.section == "menu"


def test_student_homework_back_kb_callback():
    markup = student_homework_back_kb()
    rows = markup.inline_keyboard

    assert [len(r) for r in rows] == [1]
    btn = rows[0][0]
    assert btn.text == "Назад к расписанию"

    data = MenuCb.unpack(btn.callback_data)
    assert data.section == "student_schedule"
