import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AgentMessageRequest,
    AgentMessageResponse,
    ConversationMessageResponse,
    TranscribeResponse,
)
from app.auth.dependencies import require_active_subscription
from app.core.memory.service import get_recent_messages
from app.database.models.user import User
from app.database.session import get_db
from app.integrations.speech_to_text import SpeechToTextError, transcribe_browser_audio
from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.orchestrator.agent import handle_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _get_llm() -> LLMProvider:
    return get_llm_provider()


@router.post("/message", response_model=AgentMessageResponse)
async def post_message(
    payload: AgentMessageRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(_get_llm),
) -> AgentMessageResponse:
    try:
        reply = await handle_message(db, user, llm, payload.text)
    except Exception as exc:
        # Same gap as the bot channel had: an unhandled exception here used
        # to surface as a bare 500 with an internal traceback rather than a
        # clean client-facing error.
        logger.exception("handle_message failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Техническая ошибка при обработке сообщения",
        ) from exc
    return AgentMessageResponse(reply=reply)


@router.get("/messages", response_model=list[ConversationMessageResponse])
async def get_messages(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationMessageResponse]:
    # Same history handle_message itself reads (app/core/memory/service.py) -
    # the Mini App chat and the Telegram bot share one conversation per user,
    # not a per-channel one, so reopening this screen shows what was said
    # either way.
    messages = await get_recent_messages(db, user.id)
    return [ConversationMessageResponse.model_validate(m) for m in messages]


@router.post("/transcribe", response_model=TranscribeResponse)
async def post_transcribe(
    file: UploadFile = File(...),
    user: User = Depends(require_active_subscription),
) -> TranscribeResponse:
    audio_bytes = await file.read()
    try:
        text = await transcribe_browser_audio(audio_bytes)
    except SpeechToTextError as exc:
        logger.exception("voice transcription failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось распознать голос",
        ) from exc
    return TranscribeResponse(text=text)
