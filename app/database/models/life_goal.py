import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin

# Fixed horizon set the user asked for - not freeform, so "цели на 1/3/6 "
# "месяцев / 1/3/5/10/20/30 лет" stays a small, consistently groupable set
# instead of the model inventing arbitrary horizons (2 years, 15 years,
# ...). Stored in MONTHS (not years) specifically so the short horizons
# (1/3/6 months) are exact integers rather than awkward year fractions.
LIFE_GOAL_HORIZONS_MONTHS = (1, 3, 6, 12, 36, 60, 120, 240, 360)


class LifeGoal(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "life_goals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # NULL means "Задачи на жизнь" - a lifetime goal with no specific
    # horizon, distinct from (and sorted after) the fixed month buckets.
    horizon_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Explicit calendar deadline - auto-computed for calendar-anchored
    # horizons (week, year-scale) and user-chosen for a specific month, so
    # the UI can group/label by a real date instead of a relative bucket.
    # See app/core/life_goals/service.py's target_date_for_horizon.
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Set once carry_forward_life_goals rolls this goal's expired
    # target_date onto its next period - lets the Mini App group it under
    # "Перенесённые с прошлого месяца" instead of blending back into a
    # fresh-looking bucket (see frontend/src/LifeGoals.tsx).
    carried_over: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
