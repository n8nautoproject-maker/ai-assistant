from functools import lru_cache

from app.embeddings.base import EmbeddingProvider
from app.embeddings.local_provider import LocalEmbeddingProvider


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    # Cached: loading the sentence-transformers model is expensive and it
    # should happen once per process, not once per request.
    return LocalEmbeddingProvider()
