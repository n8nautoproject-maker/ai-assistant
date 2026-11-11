import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import UUIDPKMixin


class OAuthConnectToken(UUIDPKMixin, Base):
    """Short-lived, single-use token bridging the bot channel to the
    stateless web OAuth callback: the bot sends a link containing this
    row's id, the connect/callback routes use it both to know which user
    is connecting (the callback has no other way to identify them) and as
    the OAuth `state` anti-CSRF value - one id serves both purposes.
    """

    __tablename__ = "oauth_connect_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
