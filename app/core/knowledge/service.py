import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.document import Document
from app.database.models.document_chunk import DocumentChunk
from app.embeddings.base import EmbeddingProvider

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
DEFAULT_TOP_K = 5
# A save_document call embeds every chunk synchronously (within one HTTP/
# tool request) via sentence-transformers on the CPU shared with the rest
# of this process - an unbounded content length turns one request into a
# CPU-bound stall lasting tens of seconds to minutes for everyone else
# being served by the same worker, not just the caller. 100k chars is
# already generous for a personal note.
MAX_CONTENT_LENGTH = 100_000


class NoteTooLongError(Exception):
    pass


class EmptyNoteError(Exception):
    pass


def chunk_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return chunks


async def save_document(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    user_id: uuid.UUID,
    title: str,
    content: str,
) -> Document:
    if len(content) > MAX_CONTENT_LENGTH:
        raise NoteTooLongError(
            f"content is {len(content)} chars, over the {MAX_CONTENT_LENGTH} limit"
        )
    chunks = chunk_text(content)
    if not chunks:
        # Empty/whitespace-only content - would otherwise create a Document
        # with zero DocumentChunks: it looks saved (a real row, a normal
        # success response) but can never be found by search(), since
        # search only ever matches against chunks.
        raise EmptyNoteError("content is empty")

    document = Document(user_id=user_id, title=title)
    session.add(document)
    await session.flush()

    vectors = await embeddings.embed(chunks)
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        session.add(
            DocumentChunk(
                document_id=document.id,
                user_id=user_id,
                chunk_index=i,
                content=chunk,
                embedding=vector,
            )
        )

    await session.commit()
    await session.refresh(document)
    return document


async def search(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    user_id: uuid.UUID,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[DocumentChunk]:
    (query_vector,) = await embeddings.embed([query])
    result = await session.execute(
        select(DocumentChunk)
        .where(DocumentChunk.user_id == user_id)
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )
    return list(result.scalars().all())


async def list_documents(session: AsyncSession, user_id: uuid.UUID) -> list[Document]:
    result = await session.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())
