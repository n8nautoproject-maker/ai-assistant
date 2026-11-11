import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class ScheduledJob(UUIDPKMixin, TimestampMixin, Base):
    """A proactive trigger for a user (e.g. morning check-in, focus follow-up).

    Consumed by the scheduler, which fires job_type-specific handlers at
    next_run_at (always timezone-aware, computed in the user's own timezone).
    """

    __tablename__ = "scheduled_jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recurrence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
