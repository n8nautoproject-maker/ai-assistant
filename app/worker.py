"""Entry point for the bot + scheduler process.

Runs separately from the FastAPI app (app/main.py): the API serves future
web/mobile clients, this process owns the Telegram long-polling connection
and the proactive-trigger scheduler. Run with: python -m app.worker
"""

import asyncio

from app.channels.telegram_bot.bot import run_polling
from app.embeddings.factory import get_embedding_provider
from app.logging_config import setup_logging
from app.scheduler.scheduler import start_scheduler

setup_logging()


async def main() -> None:
    # Loads the sentence-transformers model now (a few seconds, blocking)
    # instead of stalling the first user message that needs it.
    get_embedding_provider()
    start_scheduler()
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
