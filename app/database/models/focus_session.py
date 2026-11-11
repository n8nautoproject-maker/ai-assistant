import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class FocusSessionStatus(str, enum.Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    AWAITING_FOLLOWUP = "awaiting_followup"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class FocusSession(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "focus_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=FocusSessionStatus.PLANNED.value, index=True
    )
    planned_duration_min: Mapped[int] = mapped_column(Integer, default=90)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    interruption_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
