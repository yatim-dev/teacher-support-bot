from aiogram.filters.callback_data import CallbackData


class MenuCb(CallbackData, prefix="m"):
    section: str  # admin|student_schedule|parent_children|tz


class AdminCb(CallbackData, prefix="a"):
    action: str  # students|student|create_student|lesson_add|add_rule|keys_student|keys_parent|lessons
    student_id: int | None = None
    page: int = 1


class LessonCb(CallbackData, prefix="l"):
    action: str   # done|cancel|next|prev|delete_series
    lesson_id: int
    student_id: int | None = None
    offset: int = 0


class ChargeCb(CallbackData, prefix="c"):
    action: str  # paid
    charge_id: int


class ChildCb(CallbackData, prefix="ch"):
    student_id: int


class TzCb(CallbackData, prefix="tz"):
    value: str

class FsmNavCb(CallbackData, prefix="fsm"):
    action: str           # back|cancel
    flow: str             # add_rule|add_single|create_student
    student_id: int | None = None

class HomeworkCb(CallbackData, prefix="hw"):
    action: str          # view|edit|grade|back
    lesson_id: int
    student_id: int
    offset: int = 0