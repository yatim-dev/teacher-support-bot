from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, update
from zoneinfo import ZoneInfo

from ..models import User
from ..keyboards import main_menu, tz_kb
from ..callbacks import MenuCb, TzCb

router = Router()


async def get_user(session, tg_id: int) -> User:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one()


def _menu_text(user: User) -> str:
    if user.role.value == "teacher":
        return (
            "Меню (Учитель)\n\n"
            "Рекомендуемый порядок:\n"
            "1) Админка → Создать ученика\n"
            "2) В карточке ученика → Добавить правило расписания\n"
            "3) Там же → Сгенерировать ключи ученика и родителя\n\n"
            "Часовой пояс влияет на время в напоминаниях."
        )
    if user.role.value == "student":
        return "Меню (Ученик)\n\nЗдесь можно посмотреть расписание. Напоминания придут автоматически."
    return "Меню (Родитель)\n\nВыберите ребёнка и смотрите расписание. Напоминания придут автоматически."


async def show_menu(message: Message, session, user: User, *, edit: bool) -> None:
    # если TZ не выбран — уводим в выбор TZ
    if not user.timezone:
        if edit:
            await message.edit_text("Сначала выберите часовой пояс:", reply_markup=tz_kb())
        else:
            await message.answer("Сначала выберите часовой пояс:", reply_markup=tz_kb())
        return

    text = _menu_text(user)
    markup = main_menu(user.role.value)

    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            # частые случаи: "message is not modified" или "can't edit message"
            if ("message is not modified" in str(e)) or ("can't edit message" in str(e)):
                return
            raise
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"/menu", "Меню"}))
async def menu(message: Message, session):
    user = await get_user(session, message.from_user.id)
    await show_menu(message, session, user, edit=False)


# <<< ВОТ ЭТО ВАЖНО: меню по callback для кнопки "Назад" >>>
@router.callback_query(MenuCb.filter(F.section == "menu"))
async def menu_inline(call: CallbackQuery, session):
    user = await get_user(session, call.from_user.id)
    await show_menu(call.message, session, user, edit=True)
    await call.answer()


@router.callback_query(MenuCb.filter(F.section == "tz"))
async def tz_menu(call: CallbackQuery, session):
    await call.message.edit_text("Выберите ваш часовой пояс:", reply_markup=tz_kb())
    await call.answer()


@router.callback_query(TzCb.filter())
async def tz_set(call: CallbackQuery, callback_data: TzCb, session):
    ZoneInfo(callback_data.value)  # валидация

    await session.execute(
        update(User).where(User.tg_id == call.from_user.id).values(timezone=callback_data.value)
    )
    await session.commit()

    # можно сразу показать меню, вместо "Напишите /menu"
    user = await get_user(session, call.from_user.id)
    await show_menu(call.message, session, user, edit=True)
    await call.answer()


@router.callback_query(MenuCb.filter(F.section == "help"))
async def help_inline(call: CallbackQuery, session):
    user = await get_user(session, call.from_user.id)

    text = (
        "Помощь\n\n"
        "Учитель:\n"
        "1) Админка → Создать ученика\n"
        "2) В карточке ученика → Добавить занятие (разовое/еженедельное)\n"
        "3) Там же → Сгенерировать ключи ученика и родителя\n"
        "4) В «Ближайших уроках» → «Проведён»\n\n"
        "Ученик/Родитель:\n"
        "• /menu → Расписание/Дети\n"
        "• Напоминания приходят автоматически\n\n"
        "Проверьте часовой пояс в меню."
    )

    try:
        await call.message.edit_text(text, reply_markup=main_menu(user.role.value))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await call.answer()