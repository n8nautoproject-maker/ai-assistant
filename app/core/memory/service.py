import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.conversation_message import ConversationMessage, MessageRole
from app.database.models.user import User
from app.database.models.user_fact import UserFact
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Verbatim messages kept in the prompt on every turn.
HISTORY_WINDOW = 20

# Once stored history grows past this, the oldest rows (everything beyond
# HISTORY_WINDOW) get folded into user.memory_summary and deleted - keeps
# the per-request prompt bounded regardless of how long the user has been
# talking to the assistant.
SUMMARIZE_THRESHOLD = 30

SUMMARY_PROMPT = (
    "Ты обновляешь краткую справку о пользователе для другого ассистента. "
    "Ниже — предыдущая справка (может быть пустой) и новые реплики диалога. "
    "Перепиши справку заново: сохрани всё важное для дальнейшего общения "
    "(факты о пользователе, договорённости, открытые темы), убери то, что "
    "утратило значение. Пиши по-русски, сжато, простым текстом без "
    "форматирования, не длиннее 800 символов."
)


async def append_message(session: AsyncSession, user: User, role: str, content: str) -> None:
    session.add(ConversationMessage(user_id=user.id, role=role, content=content))
    # Persistent counter, separate from the row just added above - that row
    # (and everything older than HISTORY_WINDOW) gets deleted once
    # maybe_summarize prunes it, but this count must not go back down.
    if role == MessageRole.USER.value:
        user.message_count += 1
    await session.commit()


async def get_recent_messages(
    session: AsyncSession, user_id: uuid.UUID, limit: int = HISTORY_WINDOW
) -> list[ConversationMessage]:
    result = await session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.user_id == user_id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def upsert_fact(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    value: str,
    source: str = "user",
) -> None:
    stmt = insert(UserFact).values(user_id=user_id, key=key, value=value, source=source)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_facts_user_id_key",
        set_={"value": value, "source": source, "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.commit()


async def list_facts(session: AsyncSession, user_id: uuid.UUID) -> list[UserFact]:
    result = await session.execute(
        select(UserFact).where(UserFact.user_id == user_id).order_by(UserFact.key)
    )
    return list(result.scalars().all())


async def maybe_summarize(session: AsyncSession, user: User, llm: LLMProvider) -> None:
    # Row lock scoped to this read-summarize-write sequence: without it, two
    # concurrent handle_message calls for the same user (fast double-send,
    # Telegram redelivery) both read the same memory_summary/history, both
    # fold+call the LLM independently, and whichever commits last silently
    # overwrites the other's summary while both delete the same source rows.
    # Blocking here means the second caller re-reads post-commit state (lower
    # total, updated summary) instead of clobbering it.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())

    count_result = await session.execute(
        select(func.count())
        .select_from(ConversationMessage)
        .where(ConversationMessage.user_id == user.id)
    )
    total = count_result.scalar_one()
    if total <= SUMMARIZE_THRESHOLD:
        return

    to_fold = total - HISTORY_WINDOW
    old_result = await session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.user_id == user.id)
        .order_by(ConversationMessage.created_at.asc())
        .limit(to_fold)
    )
    old_messages = list(old_result.scalars().all())
    if not old_messages:
        return

    transcript = "\n".join(f"{m.role}: {m.content}" for m in old_messages)
    prompt_messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": (
                f"Предыдущая справка:\n{user.memory_summary or '(пусто)'}\n\n"
                f"Новые реплики:\n{transcript}"
            ),
        },
    ]
    response = await llm.chat(prompt_messages)
    if not response.content:
        # The LLM returned nothing to fold into the summary this time (rate
        # limit, a safety filter, a genuinely empty completion) - old_messages
        # is the only place this stretch of conversation still exists. The
        # original code deleted them regardless, permanently losing that
        # detail with no way to retry. Leaving them in place means the very
        # next call to this function (the next user message, once total is
        # still past SUMMARIZE_THRESHOLD) just retries the same fold instead.
        logger.warning("memory summarization returned empty content for user %s - retrying next call", user.id)
        return
    user.memory_summary = response.content

    old_ids = [m.id for m in old_messages]
    await session.execute(
        delete(ConversationMessage).where(ConversationMessage.id.in_(old_ids))
    )
    await session.commit()
