"""Outbound push, used by the scheduler to deliver proactive messages.

Shares the single Bot instance from bot.py rather than creating a second
one, since aiogram Bot instances hold their own HTTP session.
"""

from app.channels.telegram_bot.bot import bot


async def send_message(telegram_id: int, text: str) -> None:
    await bot.send_message(chat_id=telegram_id, text=text)
