from aiogram.types import InlineKeyboardMarkup

from app.keyboards import main_menu, admin_menu, add_lesson_type_kb, fsm_nav_kb
from app.callbacks import MenuCb, AdminCb, FsmNavCb


def _all_cb(markup: InlineKeyboardMarkup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_main_menu_teacher_has_admin_button():
    kb = main_menu("teacher")
    cbs = _all_cb(kb)
    assert MenuCb(section="admin").pack() in cbs


def test_admin_menu_has_students_and_create():
    kb = admin_menu()
    cbs = _all_cb(kb)
    assert AdminCb(action="students", page=1).pack() in cbs
    assert AdminCb(action="create_student").pack() in cbs


def test_add_lesson_type_kb_contains_expected_actions():
    kb = add_lesson_type_kb(student_id=123)
    cbs = _all_cb(kb)
    assert AdminCb(action="add_single", student_id=123).pack() in cbs
    assert AdminCb(action="add_rule", student_id=123).pack() in cbs
    assert AdminCb(action="student", student_id=123).pack() in cbs


def test_fsm_nav_kb_has_back_and_cancel():
    kb = fsm_nav_kb(flow="add_rule", student_id=55)
    cbs = _all_cb(kb)
    assert FsmNavCb(action="back", flow="add_rule", student_id=55).pack() in cbs
    assert FsmNavCb(action="cancel", flow="add_rule", student_id=55).pack() in cbs
