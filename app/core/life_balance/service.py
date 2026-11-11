import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.life_balance_rating import LifeBalanceRating

# Fixed domain set (not freeform text) so ratings stay comparable across
# snapshots over time - a model-invented label ("работа" one time, "карьера"
# the next) would silently break the delta comparison in
# format_wheel_summary. The dict on the model itself stays freeform for
# schema flexibility; this is the app-level convention layered on top.
DOMAIN_LABELS_RU = {
    "health": "Здоровье",
    "career": "Карьера/работа",
    "finance": "Финансы",
    "relationships": "Отношения/семья",
    "personal_growth": "Личностный рост",
    "recreation": "Отдых/развлечения",
    "environment": "Окружение/дом",
    "meaning": "Смысл/духовность",
}


async def save_rating(
    session: AsyncSession, user_id: uuid.UUID, ratings: dict[str, int]
) -> LifeBalanceRating:
    rating = LifeBalanceRating(user_id=user_id, ratings=ratings)
    session.add(rating)
    await session.commit()
    await session.refresh(rating)
    return rating


async def get_recent_ratings(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 2
) -> list[LifeBalanceRating]:
    """Newest first."""
    result = await session.execute(
        select(LifeBalanceRating)
        .where(LifeBalanceRating.user_id == user_id)
        .order_by(LifeBalanceRating.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def format_wheel_summary(ratings_history: list[LifeBalanceRating], tz_name: str = "UTC") -> str:
    """ratings_history must be newest-first (as returned by
    get_recent_ratings). Surfaces the lowest-scoring domain and, if a prior
    snapshot exists, the deltas per domain - grounding for the model to
    reference instead of re-deriving from raw numbers itself.

    tz_name: created_at is stored UTC - displaying it raw showed the wrong
    calendar date for users east of UTC (most of Russia) shortly after
    local midnight, same bug already fixed for the task list formatter.
    """
    if not ratings_history:
        return "Оценок колеса баланса пока нет."

    tz = ZoneInfo(tz_name)
    current = ratings_history[0]
    lines = [f"Текущая оценка ({current.created_at.astimezone(tz):%Y-%m-%d}):"]
    for domain, score in current.ratings.items():
        label = DOMAIN_LABELS_RU.get(domain, domain)
        lines.append(f"- {label}: {score}/10")

    if current.ratings:
        lowest_domain = min(current.ratings, key=current.ratings.get)
        lowest_label = DOMAIN_LABELS_RU.get(lowest_domain, lowest_domain)
        lines.append(f"Самая низкая сфера сейчас: {lowest_label} ({current.ratings[lowest_domain]}/10).")

    if len(ratings_history) > 1:
        previous = ratings_history[1]
        deltas = []
        for domain, score in current.ratings.items():
            if domain in previous.ratings:
                diff = score - previous.ratings[domain]
                if diff != 0:
                    label = DOMAIN_LABELS_RU.get(domain, domain)
                    sign = "+" if diff > 0 else ""
                    deltas.append(f"{label}: {sign}{diff}")
        if deltas:
            lines.append(
                f"Изменения с прошлой оценки ({previous.created_at.astimezone(tz):%Y-%m-%d}): "
                + "; ".join(deltas)
            )
        else:
            lines.append("С прошлой оценки существенных изменений нет.")

    return "\n".join(lines)
