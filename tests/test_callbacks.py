from app.callbacks import MenuCb, AdminCb, LessonCb, LessonPayCb, ChildCb, TzCb, FsmNavCb, HomeworkCb


def test_menu_cb_roundtrip():
    cb = MenuCb(section="admin")
    packed = cb.pack()
    restored = MenuCb.unpack(packed)
    assert restored.section == "admin"


def test_admin_cb_roundtrip():
    cb = AdminCb(action="student", student_id=10, page=2)
    packed = cb.pack()
    restored = AdminCb.unpack(packed)
    assert restored.action == "student"
    assert restored.student_id == 10
    assert restored.page == 2


def test_lesson_cb_roundtrip():
    cb = LessonCb(action="done", lesson_id=5, student_id=10, offset=3)
    packed = cb.pack()
    restored = LessonCb.unpack(packed)
    assert restored.action == "done"
    assert restored.lesson_id == 5
    assert restored.student_id == 10
    assert restored.offset == 3


def test_lesson_pay_cb_roundtrip():
    cb = LessonPayCb(action="paid", lesson_id=7, student_id=10, offset=3)
    packed = cb.pack()
    restored = LessonPayCb.unpack(packed)
    assert restored.action == "paid"
    assert restored.lesson_id == 7
    assert restored.student_id == 10
    assert restored.offset == 3


def test_child_cb_roundtrip():
    cb = ChildCb(student_id=42)
    packed = cb.pack()
    restored = ChildCb.unpack(packed)
    assert restored.student_id == 42


def test_tz_cb_roundtrip():
    cb = TzCb(value="Europe/Moscow")
    packed = cb.pack()
    restored = TzCb.unpack(packed)
    assert restored.value == "Europe/Moscow"


def test_fsm_nav_cb_roundtrip():
    cb = FsmNavCb(action="cancel", flow="add_rule", student_id=1)
    packed = cb.pack()
    restored = FsmNavCb.unpack(packed)
    assert restored.action == "cancel"
    assert restored.flow == "add_rule"
    assert restored.student_id == 1


def test_homework_cb_roundtrip():
    cb = HomeworkCb(action="view", homework_id=1, student_id=2, offset=0)
    packed = cb.pack()
    restored = HomeworkCb.unpack(packed)
    assert restored.action == "view"
    assert restored.homework_id == 1
    assert restored.student_id == 2
    assert restored.offset == 0

