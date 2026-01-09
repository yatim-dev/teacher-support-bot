from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, update
from zoneinfo import ZoneInfo

from ..models import User, Role
from ..keyboards import main_menu, tz_kb
from ..callbacks import MenuCb, TzCb

router = Router()


async def get_user_or_none(session, tg_id: int) -> User | None:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def get_user(session, tg_id: int) -> User:
    user = await get_user_or_none(session, tg_id)
    if not user:
        # Если у вас есть поток, где /menu доступно только после регистрации — можно заменить на более мягкое сообщение.
        raise ValueError("User not found. Register first.")
    return user


def menu_text(user: User) -> str:
    if user.role == Role.teacher:
        return (
            "Меню (Учитель)\n\n"
            "Рекомендуемый порядок:\n"
            "1) Админка → Создать ученика\n"
            "2) В карточке ученика → Добавить правило расписания\n"
            "3) Там же → Сгенерировать ключи ученика и родителя\n\n"
            "Часовой пояс влияет на время в напоминаниях."
        )
    if user.role == Role.student:
        return "Меню (Ученик)\n\nЗдесь можно посмотреть расписание. Напоминания придут автоматически."
    return "Меню (Родитель)\n\nВыберите ребёнка и смотрите расписание. Напоминания придут автоматически."


def help_text(user: User | None) -> str:
    # если не зарегистрирован — покажем базовую справку
    if user is None:
        return (
            "Помощь\n\n"
            "Команды:\n"
            "/start — регистрация / вход по ключу\n"
            "/menu — меню\n"
            "/help — помощь\n\n"
            "Сначала зарегистрируйтесь через /start и ключ."
        )

    base = (
        "Помощь\n\n"
        "Команды:\n"
        "/menu — меню\n"
        "/help — помощь\n"
    )

    if user.role == Role.teacher:
        return (
            base
            + "\nУчитель:\n"
              "1) Админка → Создать ученика\n"
              "2) В карточке ученика → Добавить правило расписания\n"
              "3) Там же → Сгенерировать ключи ученика и родителя\n"
              "4) В «Ближайших уроках» → «Проведён»\n"
              "5) Получаете уведомления, когда ученик нажимает «Задание выполнено»\n\n"
              "Проверьте часовой пояс в меню."
        )

    if user.role == Role.student:
        return (
            base
            + "\nУченик:\n"
              "• /menu → Расписание\n"
              "• В карточке ДЗ можно нажать «Задание выполнено» (учителю придёт уведомление)\n"
              "• Напоминания приходят автоматически\n\n"
              "Проверьте часовой пояс в меню."
        )

    # parent
    return (
        base
        + "\nРодитель:\n"
          "• /menu → Дети → выбрать ребёнка → расписание\n"
          "• Можно смотреть расписание и ДЗ\n"
          "• Напоминания приходят автоматически\n\n"
          "Проверьте часовой пояс в меню."
    )


async def safe_edit(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        s = str(e)
        if ("message is not modified" in s) or ("can't edit message" in s):
            return
        raise


async def show_menu(message: Message, session, user: User, *, edit: bool) -> None:
    # если TZ не выбран — уводим в выбор TZ
    if not user.timezone:
        if edit:
            await safe_edit(message, "Сначала выберите часовой пояс:", reply_markup=tz_kb())
        else:
            await message.answer("Сначала выберите часовой пояс:", reply_markup=tz_kb())
        return

    text = menu_text(user)
    markup = main_menu(user.role.value)

    if edit:
        await safe_edit(message, text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text.in_({"/menu", "Меню"}))
async def menu(message: Message, session):
    user = await get_user(session, message.from_user.id)
    await show_menu(message, session, user, edit=False)


@router.callback_query(MenuCb.filter(F.section == "menu"))
async def menu_inline(call: CallbackQuery, session):
    user = await get_user(session, call.from_user.id)
    await show_menu(call.message, session, user, edit=True)
    await call.answer()


@router.callback_query(MenuCb.filter(F.section == "tz"))
async def tz_menu(call: CallbackQuery, session):
    await safe_edit(call.message, "Выберите ваш часовой пояс:", reply_markup=tz_kb())
    await call.answer()


@router.callback_query(TzCb.filter())
async def tz_set(call: CallbackQuery, callback_data: TzCb, session):
    ZoneInfo(callback_data.value)  # валидация

    await session.execute(
        update(User)
        .where(User.tg_id == call.from_user.id)
        .values(timezone=callback_data.value)
    )
    await session.commit()

    user = await get_user(session, call.from_user.id)
    await show_menu(call.message, session, user, edit=True)

    # (опционально) короткое уведомление, не меняя экран
    await call.answer(f"Часовой пояс установлен: {callback_data.value}")


# /help как команда
@router.message(Command("help"))
async def help_cmd(message: Message, session):
    user = await get_user_or_none(session, message.from_user.id)
    await message.answer(help_text(user))


# help как кнопка в меню (callback)
@router.callback_query(MenuCb.filter(F.section == "help"))
async def help_inline(call: CallbackQuery, session):
    user = await get_user_or_none(session, call.from_user.id)
    await safe_edit(call.message, help_text(user), reply_markup=main_menu(user.role.value if user else "student"))
    await call.answer()
