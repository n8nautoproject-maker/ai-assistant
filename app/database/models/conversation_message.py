import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import UUIDPKMixin


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationMessage(UUIDPKMixin, Base):
    """One persisted turn of the user-visible dialogue.

    Only final user/assistant turns are stored here, not the intermediate
    tool-call/tool-result messages the orchestrator exchanges with the LLM
    within a single handle_message call - those are implementation detail,
    not conversation content worth remembering. Immutable once written, so
    no updated_at (unlike TimestampMixin-based models).
    """

    __tablename__ = "conversation_messages"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
