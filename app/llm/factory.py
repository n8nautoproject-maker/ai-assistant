from app.config import get_settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.openai_provider import OpenAIProvider


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    if settings.llm_provider == "openai":
        extra_headers = None
        # Gate on the base_url actually pointing at Yandex, not just on
        # yandex_folder_id being non-empty - restoring yandex_folder_id to
        # fix voice transcription (SpeechKit reads a separate field, see
        # yandex_speechkit_folder_id) would otherwise silently attach these
        # Yandex-only headers to every DeepSeek/OpenAI request again.
        if settings.yandex_folder_id and "yandex" in settings.llm_base_url.lower():
            # Yandex Cloud AI Studio's OpenAI-compatible endpoint requires
            # this on every request in addition to the bearer API key.
            extra_headers = {
                "x-folder-id": settings.yandex_folder_id,
                "x-data-logging-enabled": "false",
            }
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.llm_base_url,
            extra_headers=extra_headers,
        )
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
