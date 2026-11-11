"""Persistent bottom menu (Telegram reply keyboard) - Telegram-specific
presentation, so it lives in the channel layer, not core/orchestrator.
"""

from aiogram import Bot
from aiogram.types import (
    KeyboardButton,
    MenuButtonDefault,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.config import get_settings

BTN_TASKS_TODAY = "📅 Задачи на сегодня"
BTN_TASKS_TOMORROW = "🗓 Задачи на завтра"
BTN_LIFE_GOALS = "🌱 Задачи на жизнь"
BTN_SUBSCRIPTION = "💳 Подписка"
BTN_CALENDAR_APP = "📆 Планер"


def _build_main_menu() -> ReplyKeyboardMarkup:
    # No web_app button here anymore - a KeyboardButton.web_app mixed into a
    # ReplyKeyboardMarkup has inconsistent support across Telegram clients,
    # and on Desktop it was observed to make the whole keyboard fail to
    # render (not just that one button). The Mini App is exposed instead via
    # the persistent Menu Button (see set_main_menu_button below), which is
    # the client-wide element built for this and renders on every platform.
    # BTN_TASKS_TODAY/BTN_TASKS_TOMORROW are plain text that the deterministic
    # day-query fast path in bot.py's on_message already recognizes (сегодня/
    # завтра + задачи) - no special-casing needed here, they just need to be
    # phrased so that matcher catches them. "Добавить задачу" removed - just
    # type/say what you want added, no dedicated button needed for that.
    rows = [
        [KeyboardButton(text=BTN_TASKS_TODAY), KeyboardButton(text=BTN_TASKS_TOMORROW)],
        [KeyboardButton(text=BTN_LIFE_GOALS), KeyboardButton(text=BTN_SUBSCRIPTION)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


MAIN_MENU = _build_main_menu()


async def set_main_menu_button(bot: Bot) -> None:
    """Registers the Mini App as the bot's default chat menu button.

    Telegram only allows a web_app menu button when the URL is HTTPS - fall
    back to the default menu button on local dev (http://localhost) rather
    than calling the API with a URL Telegram would reject.
    """
    settings = get_settings()
    if settings.app_base_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=BTN_CALENDAR_APP, web_app=WebAppInfo(url=settings.app_base_url)
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())

# Buttons that just forward a canned prompt into the normal LLM pipeline -
# works because the corresponding tool (list_life_goals) already exists and
# handles the request correctly, same shortcut mechanism the removed
# Фокус-сессия/Продуктивность buttons used.
PROMPT_FOR_BUTTON: dict[str, str] = {
    BTN_LIFE_GOALS: "Покажи мои жизненные цели.",
}
