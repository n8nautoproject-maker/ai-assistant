import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import TimestampMixin, UUIDPKMixin


class IntegrationCredential(UUIDPKMixin, TimestampMixin, Base):
    """OAuth tokens for a user's connected external service (Google, ...).

    access_token/refresh_token are stored Fernet-encrypted (see
    app/integrations/crypto.py) - unlike every other token/secret in this
    project (bot token, API keys), these belong to the *user*, not the
    app, so they get encrypted at rest rather than living only in .env.
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integration_credentials_user_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
