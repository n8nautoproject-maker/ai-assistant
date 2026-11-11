from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    secret_key: str = "change-me"
    jwt_expire_minutes: int = 60 * 24 * 30

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_secret_key(cls, v: str) -> str:
        # .env.example literally ships "SECRET_KEY=change-me" as the
        # placeholder value - a bare "make this field required" wouldn't
        # catch someone who copies the example file verbatim, since the
        # field would still be "set". Failing loudly at startup instead of
        # silently accepting a publicly-known JWT secret (anyone could then
        # forge a valid access token for any user_id) - fail closed, not
        # silently insecure.
        if v == "change-me":
            raise ValueError(
                "SECRET_KEY is still the placeholder from .env.example - generate a "
                'real one (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`) '
                "and set it in .env before starting."
            )
        return v

    database_url: str = "postgresql+asyncpg://ai_assistant:ai_assistant@localhost:5433/ai_assistant_core"
    redis_url: str = "redis://localhost:6380/0"

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    @field_validator("telegram_bot_token")
    @classmethod
    def _reject_empty_bot_token(cls, v: str) -> str:
        # Both Telegram auth paths (Login Widget and Mini App initData - see
        # app/auth/telegram.py) derive their HMAC secret deterministically
        # from this token. An empty token makes that secret a known constant
        # (HMAC of an empty string), letting anyone compute a valid signature
        # for a forged initData with any telegram_id and get a JWT for
        # someone else's account - this isn't just "the bot won't answer",
        # it's the same fail-closed situation as secret_key above.
        if not v:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is empty - Telegram auth (Login Widget and "
                "Mini App initData) derives its HMAC secret from this value, so "
                "an empty token makes that secret a public, guessable constant. "
                "Set a real bot token in .env before starting."
            )
        return v

    # Telegram id of the one user allowed to see /admin/* routes (the
    # users-list table in the Mini App's Profile tab). 0 = nobody - fails
    # closed if this is left unset, rather than silently open to anyone.
    admin_telegram_id: int = 0

    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Empty = OpenAI's own endpoint. Set to an OpenAI-compatible endpoint
    # (e.g. https://openrouter.ai/api/v1) to route through a proxy provider -
    # OpenAIProvider works unchanged since the wire format is identical.
    llm_base_url: str = ""
    # Only used when routing through Yandex Cloud AI Studio's
    # OpenAI-compatible endpoint - see app/llm/factory.py.
    yandex_folder_id: str = ""
    # Yandex SpeechKit auth - kept separate from openai_api_key. They used
    # to be the same value back when llm_provider pointed at Yandex too, but
    # that stopped being true once LLM_PROVIDER switched to DeepSeek, which
    # silently broke voice transcription (wrong key + empty folder id going
    # to Yandex) until this was split out.
    yandex_api_key: str = ""
    # SAME split reasoning as yandex_api_key above, one field late: this used
    # to be the same value as yandex_folder_id (LLM routing) too. Restoring
    # yandex_folder_id to fix voice transcription (2026-07-30) would have
    # silently resumed sending Yandex-only headers (x-folder-id,
    # x-data-logging-enabled) on every DeepSeek request, since factory.py
    # used to gate on "is yandex_folder_id set" rather than "is the LLM
    # actually Yandex" - found by external review of this exact incident.
    yandex_speechkit_folder_id: str = ""

    app_base_url: str = "http://localhost:8000"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/integrations/google/callback"
    oauth_token_encryption_key: str = ""

    # Serper.dev (Google Search API wrapper) - Tavily blocks this server's
    # region deliberately (Tavily Support confirmed a compliance
    # restriction, not an IP-reputation block). Serper's own signup was
    # refused at first too, but succeeded on a later attempt (2026-08-14) -
    # simpler than Yandex Search API (sync JSON, no AI Studio org/billing
    # setup needed), so used instead.
    serper_api_key: str = ""

    # ЮKassa (YooKassa) - same value works for either the test shop or the
    # real one once approved, no code change needed to switch - see
    # app/integrations/yookassa.py.
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""

    # Anthropic Claude - used for the isolated multi-task voice-batch
    # extraction call in orchestrator/agent.py regardless of llm_provider
    # (empty = that extraction step is silently skipped, not an error), AND
    # as the main LLM_PROVIDER loop itself when llm_provider="anthropic" -
    # see app/llm/factory.py. Same key serves both; anthropic_model only
    # matters for the main loop (the extraction call has its own fixed
    # model, see _ANTHROPIC_EXTRACTION_MODEL in agent.py).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
