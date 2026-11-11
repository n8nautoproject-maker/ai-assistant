import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class UserFact(UUIDPKMixin, TimestampMixin, Base):
    """A small structured fact about the user, explicitly saved via the
    remember_fact tool - not auto-extracted. One row per key per user;
    saving an existing key overwrites the value (see upsert_fact).
    """

    __tablename__ = "user_facts"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_facts_user_id_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="user")
