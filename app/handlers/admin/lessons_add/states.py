from aiogram.fsm.state import StatesGroup, State


class AddRuleFSM(StatesGroup):
    weekday = State()
    time_local = State()
    duration = State()
    start_date = State()


class AddSingleLessonFSM(StatesGroup):
    date_ = State()
    time_ = State()
    duration = State()
