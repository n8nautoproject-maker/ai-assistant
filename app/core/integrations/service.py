import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge import service as knowledge_service
from app.database.models.document import Document
from app.database.models.integration_credential import IntegrationCredential
from app.database.models.oauth_connect_token import OAuthConnectToken
from app.embeddings.base import EmbeddingProvider
from app.integrations import gmail, google_calendar, google_drive, google_oauth
from app.integrations.crypto import decrypt, encrypt

CONNECT_TOKEN_TTL_MINUTES = 10
# Refresh proactively before actual expiry so a request never races a
# token that dies mid-call.
TOKEN_REFRESH_BUFFER_MINUTES = 5


class NotConnectedError(Exception):
    pass


class UnsupportedFileTypeError(Exception):
    pass


async def create_connect_url(
    session: AsyncSession, user_id: uuid.UUID, provider: str = "google"
) -> str:
    token = OAuthConnectToken(
        user_id=user_id,
        provider=provider,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=CONNECT_TOKEN_TTL_MINUTES),
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return google_oauth.build_authorization_url(state=str(token.id))


async def handle_oauth_callback(session: AsyncSession, state: str, code: str) -> uuid.UUID | None:
    """Returns the user_id the callback was for, or None if the state token
    is missing/expired/already used (e.g. a stale or replayed link)."""
    try:
        token_id = uuid.UUID(state)
    except ValueError:
        return None

    connect_token = await session.get(OAuthConnectToken, token_id)
    now = datetime.now(timezone.utc)
    if connect_token is None or connect_token.consumed or connect_token.expires_at < now:
        return None

    tokens = await google_oauth.exchange_code_for_tokens(code)
    expires_at = now + timedelta(seconds=tokens.expires_in)

    stmt = insert(IntegrationCredential).values(
        user_id=connect_token.user_id,
        provider=connect_token.provider,
        access_token=encrypt(tokens.access_token),
        refresh_token=encrypt(tokens.refresh_token) if tokens.refresh_token else None,
        expires_at=expires_at,
        scope=tokens.scope,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_integration_credentials_user_provider",
        set_={
            "access_token": stmt.excluded.access_token,
            # A repeat consent might not return a fresh refresh_token even
            # with prompt=consent forced - don't clobber a working one with
            # NULL if that happens.
            "refresh_token": func.coalesce(
                stmt.excluded.refresh_token, IntegrationCredential.refresh_token
            ),
            "expires_at": stmt.excluded.expires_at,
            "scope": stmt.excluded.scope,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    connect_token.consumed = True
    await session.commit()
    return connect_token.user_id


async def _get_credential(
    session: AsyncSession, user_id: uuid.UUID, provider: str
) -> IntegrationCredential | None:
    result = await session.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_id == user_id, IntegrationCredential.provider == provider
        )
    )
    return result.scalars().first()


async def _call_google(session: AsyncSession, user_id: uuid.UUID, call):
    """Awaits `call` (an already-constructed adapter coroutine) and turns a
    GoogleTokenRevokedError (401 - the user revoked access in their Google
    account before our locally-tracked access_token expiry) into the same
    NotConnectedError + stale-credential-cleanup every caller here already
    gets for a revoked REFRESH token (see get_valid_access_token) - without
    this, revoking access mid-lifetime surfaced as a generic GoogleAPIError
    that never resolved itself on retry, since the credential row (and its
    now-permanently-invalid cached access_token) was never actually removed.
    """
    try:
        return await call
    except google_oauth.GoogleTokenRevokedError:
        credential = await _get_credential(session, user_id, "google")
        if credential is not None:
            await session.delete(credential)
            await session.commit()
        raise NotConnectedError() from None


async def is_connected(session: AsyncSession, user_id: uuid.UUID, provider: str = "google") -> bool:
    return await _get_credential(session, user_id, provider) is not None


async def get_valid_access_token(
    session: AsyncSession, user_id: uuid.UUID, provider: str = "google"
) -> str | None:
    credential = await _get_credential(session, user_id, provider)
    if credential is None:
        return None

    now = datetime.now(timezone.utc)
    if (
        credential.expires_at is None
        or credential.expires_at > now + timedelta(minutes=TOKEN_REFRESH_BUFFER_MINUTES)
    ):
        return decrypt(credential.access_token)

    if credential.refresh_token is None:
        return None

    try:
        tokens = await google_oauth.refresh_access_token(decrypt(credential.refresh_token))
    except google_oauth.GoogleAPIError as exc:
        if "invalid_grant" in str(exc):
            # The refresh token itself was revoked/expired (user revoked
            # access in their Google account, or it simply died) - not a
            # transient network blip. Real incident: this used to be
            # indistinguishable from a one-off API failure, so is_connected
            # kept saying True forever and every Calendar/Gmail/Drive
            # action failed with a generic "try again later" that could
            # never actually resolve itself. Deleting the stale row and
            # raising NotConnectedError instead means every existing caller
            # (they already all catch NotConnectedError) automatically
            # offers a fresh connect link, for free.
            await session.delete(credential)
            await session.commit()
            raise NotConnectedError() from exc
        raise
    credential.access_token = encrypt(tokens.access_token)
    credential.expires_at = now + timedelta(seconds=tokens.expires_in)
    await session.commit()
    return tokens.access_token


async def create_calendar_event(
    session: AsyncSession,
    user_id: uuid.UUID,
    summary: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
) -> dict:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(
        session, user_id, google_calendar.create_event(access_token, summary, start, end, description)
    )


async def update_calendar_event(
    session: AsyncSession,
    user_id: uuid.UUID,
    event_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
) -> dict:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(
        session,
        user_id,
        google_calendar.update_event(access_token, event_id, summary, start, end, description),
    )


async def delete_calendar_event(session: AsyncSession, user_id: uuid.UUID, event_id: str) -> None:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    await _call_google(session, user_id, google_calendar.delete_event(access_token, event_id))


async def list_calendar_events(
    session: AsyncSession, user_id: uuid.UUID, time_min: datetime, time_max: datetime
) -> list[dict]:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(session, user_id, google_calendar.list_events(access_token, time_min, time_max))


async def search_drive_files(
    session: AsyncSession, user_id: uuid.UUID, name_query: str
) -> list[dict]:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(session, user_id, google_drive.search_files(access_token, name_query))


async def import_drive_document(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    user_id: uuid.UUID,
    file_id: str,
    file_name: str,
    mime_type: str,
) -> Document:
    if mime_type not in google_drive.SUPPORTED_MIME_TYPES:
        raise UnsupportedFileTypeError(mime_type)

    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()

    text = await _call_google(session, user_id, google_drive.get_file_text(access_token, file_id, mime_type))
    return await knowledge_service.save_document(
        session, embeddings, user_id=user_id, title=file_name, content=text
    )


async def list_emails(
    session: AsyncSession, user_id: uuid.UUID, query: str | None = None, max_results: int = 10
) -> list[dict]:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(
        session, user_id, gmail.list_messages(access_token, query=query, max_results=max_results)
    )


async def send_email(
    session: AsyncSession, user_id: uuid.UUID, to: str, subject: str, body: str
) -> dict:
    access_token = await get_valid_access_token(session, user_id)
    if access_token is None:
        raise NotConnectedError()
    return await _call_google(session, user_id, gmail.send_message(access_token, to, subject, body))
