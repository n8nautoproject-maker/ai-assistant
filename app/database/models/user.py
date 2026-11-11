import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class SubscriptionPlan(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")

    # City/country for namaz (prayer-time) reminders - null means the
    # feature isn't enabled. Kept separate from timezone since Aladhan
    # geocodes by city name, not by IANA timezone.
    namaz_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    namaz_country: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Subscription is stored as plain strings (not a DB enum) so plans/statuses
    # can evolve without ALTER TYPE migrations; validated at the app layer.
    subscription_plan: Mapped[str] = mapped_column(
        String(32), default=SubscriptionPlan.FREE.value
    )
    subscription_status: Mapped[str] = mapped_column(
        String(32), default=SubscriptionStatus.TRIALING.value
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reusable ЮKassa payment method saved from the first successful
    # checkout (with the user's consent, collected on ЮKassa's own hosted
    # page) - subsequent monthly charges reference this id directly, no
    # redirect/user interaction needed. Null until the first payment
    # succeeds. See app/core/billing/service.py.
    yookassa_payment_method_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Rolling summary of conversation history older than the window kept
    # verbatim in conversation_messages - see app/core/memory/service.py.
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Text of the last proactive custom_reminder push (set_interval_reminder /
    # set_recurring_reminder), if any, that hasn't been followed by a reply
    # yet. Unlike task reminders and focus-session follow-ups, a generic
    # reminder push has no dedicated state table to hang an "awaiting reply"
    # flag off of - see app/scheduler/handlers.py:_handle_custom_reminder and
    # app/orchestrator/agent.py:_reminder_reply_hint. Fire-once: cleared as
    # soon as the next message is handled.
    pending_reminder_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Persistent lifetime counter - conversation_messages is a rolling
    # window (see app/core/memory/service.py:maybe_summarize), so COUNT(*)
    # on it undercounts anyone whose older messages already got pruned.
    # Incremented in app/core/memory/service.py:append_message, never
    # decremented.
    message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Touched on every authenticated request (see app/auth/dependencies.py
    # and the Telegram bot's own message handler) - covers Mini App usage
    # (Tasks/Focus/Life Goals REST calls) that conversation_messages never
    # sees at all, not just chat turns.
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
