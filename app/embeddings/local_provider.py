import asyncio

from sentence_transformers import SentenceTransformer

from app.embeddings.base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """Runs a multilingual sentence-transformers model on-machine - no API
    key, no network dependency, no Russia-reachability concerns (unlike
    the LLM, which is routed through OpenRouter because OpenAI direct
    access doesn't work from Russia). Model is loaded once per process.
    """

    def __init__(self, model_name: str = "paraphrase-multilingual-mpnet-base-v2"):
        self._model = SentenceTransformer(model_name)
        self.dimensions = self._model.get_embedding_dimension()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # encode() is a blocking CPU-bound call - offload it so it doesn't
        # stall the event loop the rest of the app (bot, API) shares.
        vectors = await asyncio.to_thread(self._model.encode, texts)
        return [v.tolist() for v in vectors]
