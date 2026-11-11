import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class LifeBalanceRating(UUIDPKMixin, TimestampMixin, Base):
    """One "wheel of life balance" snapshot - a user-rated 1-10 score per
    life domain, taken at a point in time. Domains are freeform keys in
    `ratings` (not a fixed enum/columns) so the set of areas can evolve
    without a migration, same flexibility principle as ScheduledJob.payload.
    """

    __tablename__ = "life_balance_ratings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ratings: Mapped[dict] = mapped_column(JSONB, default=dict)
