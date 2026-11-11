import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.PENDING.value)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Separate from due_at (a deadline) - this is "notify me at this exact
    # moment", e.g. "напомни принять душ в 8:40". One-time only for v1; see
    # app/core/tasks/service.py for the scheduled_job it creates.
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set true when a reminder fires and the check-in question ("получилось
    # сделать?") is sent - the next message is then interpreted against this
    # task instead of ambiguous free text. Cleared after one exchange
    # regardless of the answer, see app/orchestrator/agent.py.
    awaiting_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set once this task has a due_at/remind_at and Google Calendar is
    # connected - lets create_task/update_task/complete_task/delete_task
    # update or remove the same event instead of creating a new one every
    # time (see _sync_calendar_event in app/core/tasks/service.py).
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # User-designated priority for *today*, set by answering the morning
    # check-in's question (see _handle_morning_checkin in
    # app/scheduler/handlers.py) - not a persistent property, cleared and
    # re-asked fresh every morning.
    is_priority: Mapped[bool] = mapped_column(Boolean, default=False)
