from aiogram.types import InlineKeyboardMarkup

from app.keyboards import (
    tz_kb, TZ_LIST,
    students_list_kb,
    student_card_kb,
    lesson_actions_kb,
    parent_children_kb,
    after_rule_added_kb, after_single_added_kb,
    student_delete_confirm_kb,
    homework_kb,
)
from app.callbacks import TzCb, AdminCb, LessonCb, LessonPayCb, ChildCb, HomeworkCb


def _btns(markup: InlineKeyboardMarkup):
    return [btn for row in markup.inline_keyboard for btn in row]


def _cbs(markup: InlineKeyboardMarkup):
    return [btn.callback_data for btn in _btns(markup)]


def test_tz_kb_contains_all_timezones():
    kb = tz_kb()
    cbs = _cbs(kb)

    assert len(cbs) == len(TZ_LIST)
    for tz in TZ_LIST:
        assert TzCb(value=tz).pack() in cbs


def test_students_list_kb_contains_students_and_pagination_buttons():
    rows = [(1, "A"), (2, "B")]
    kb = students_list_kb(rows, page=3)
    cbs = _cbs(kb)

    assert AdminCb(action="student", student_id=1).pack() in cbs
    assert AdminCb(action="student", student_id=2).pack() in cbs
    assert AdminCb(action="students", page=2).pack() in cbs  # prev
    assert AdminCb(action="students", page=4).pack() in cbs  # next


def test_students_list_kb_prev_page_not_less_than_1():
    kb = students_list_kb([(1, "A")], page=1)
    cbs = _cbs(kb)
    assert AdminCb(action="students", page=1).pack() in cbs  # prev stays 1


def test_student_card_kb_has_expected_actions():
    sid = 123
    kb = student_card_kb(sid)
    cbs = _cbs(kb)

    assert AdminCb(action="lessons_add", student_id=sid).pack() in cbs
    assert AdminCb(action="keys_student", student_id=sid).pack() in cbs
    assert AdminCb(action="keys_parent", student_id=sid).pack() in cbs
    assert AdminCb(action="lessons", student_id=sid).pack() in cbs
    assert AdminCb(action="student_delete", student_id=sid).pack() in cbs
    assert AdminCb(action="students", page=1).pack() in cbs


def test_lesson_actions_kb_has_delete_series_only_when_recurring():
    kb1 = lesson_actions_kb(lesson_id=10, student_id=20, offset=3, is_recurring=False)
    cbs1 = _cbs(kb1)
    assert LessonCb(action="delete_series", lesson_id=10, student_id=20, offset=3).pack() not in cbs1

    kb2 = lesson_actions_kb(lesson_id=10, student_id=20, offset=3, is_recurring=True)
    cbs2 = _cbs(kb2)
    assert LessonCb(action="delete_series", lesson_id=10, student_id=20, offset=3).pack() in cbs2


def test_lesson_actions_kb_pay_button_present_only_when_show_pay_true():
    # show_pay=False -> кнопки оплаты нет
    kb1 = lesson_actions_kb(lesson_id=10, student_id=20, offset=0, is_recurring=False, show_pay=False)
    cbs1 = _cbs(kb1)
    assert LessonPayCb(action="paid", lesson_id=10, student_id=20, offset=0).pack() not in cbs1

    # show_pay=True -> кнопка оплаты есть
    kb2 = lesson_actions_kb(lesson_id=10, student_id=20, offset=0, is_recurring=False, show_pay=True)
    cbs2 = _cbs(kb2)
    assert LessonPayCb(action="paid", lesson_id=10, student_id=20, offset=0).pack() in cbs2


def test_parent_children_kb_contains_children():
    kb = parent_children_kb(children=[(1, "Child A"), (2, "Child B")])
    cbs = _cbs(kb)
    assert ChildCb(student_id=1).pack() in cbs
    assert ChildCb(student_id=2).pack() in cbs


def test_after_rule_added_kb_contains_lessons_and_student():
    sid = 5
    kb = after_rule_added_kb(student_id=sid)
    cbs = _cbs(kb)
    assert AdminCb(action="lessons", student_id=sid).pack() in cbs
    assert AdminCb(action="student", student_id=sid).pack() in cbs


def test_after_single_added_kb_contains_lessons_and_student():
    sid = 5
    kb = after_single_added_kb(student_id=sid)
    cbs = _cbs(kb)
    assert AdminCb(action="lessons", student_id=sid).pack() in cbs
    assert AdminCb(action="student", student_id=sid).pack() in cbs


def test_student_delete_confirm_kb_contains_confirm_and_back():
    sid = 9
    kb = student_delete_confirm_kb(student_id=sid)
    cbs = _cbs(kb)
    assert AdminCb(action="student_delete_confirm", student_id=sid).pack() in cbs
    assert AdminCb(action="student", student_id=sid).pack() in cbs


def test_homework_kb_contains_edit_grade_back():
    kb = homework_kb(homework_id=1, student_id=2, offset=3)
    cbs = _cbs(kb)
    assert HomeworkCb(action="edit", homework_id=1, student_id=2, offset=3).pack() in cbs
    assert HomeworkCb(action="grade", homework_id=1, student_id=2, offset=3).pack() in cbs
    assert HomeworkCb(action="back", homework_id=1, student_id=2, offset=3).pack() in cbs
