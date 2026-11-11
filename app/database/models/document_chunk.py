import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.common import UUIDPKMixin

# Must match LocalEmbeddingProvider.dimensions (app/embeddings/local_provider.py).
# Changing the embedding model/dimension requires a new migration and
# re-embedding every existing chunk.
EMBEDDING_DIMENSIONS = 768


class DocumentChunk(UUIDPKMixin, Base):
    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from documents.user_id so search queries can filter by
    # user without a join - every knowledge lookup is user-scoped.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
