from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Swapping the embedding model/provider means writing one new class
    here - Knowledge Base code only depends on this interface, not on
    sentence-transformers directly. dimensions must match the vector
    column size in document_chunks (migration 0004).
    """

    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
