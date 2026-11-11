from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    FocusAbandonRequest,
    FocusCompleteRequest,
    FocusSessionResponse,
    FocusStartRequest,
)
from app.auth.dependencies import require_active_subscription
from app.core.focus.service import (
    DEFAULT_DURATION_MIN,
    abandon_session,
    complete_session,
    get_open_session,
    list_sessions,
    start_session,
)
from app.database.models.user import User
from app.database.session import get_db

router = APIRouter(prefix="/focus", tags=["focus"])

# Session lifecycle here mirrors the LLM tools in tools_registry.py
# (start_focus_session/complete_focus_session/abandon_focus_session) - the
# Mini App's focus screen is a one-tap alternative to the same
# conversational flow, not a separate feature.


@router.get("/sessions", response_model=list[FocusSessionResponse])
async def get_sessions(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[FocusSessionResponse]:
    sessions = await list_sessions(db, user.id)
    return [FocusSessionResponse.model_validate(s) for s in sessions]


@router.get("/sessions/open", response_model=FocusSessionResponse | None)
async def get_open(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> FocusSessionResponse | None:
    focus_session = await get_open_session(db, user.id)
    return FocusSessionResponse.model_validate(focus_session) if focus_session else None


@router.post("/sessions/start", response_model=FocusSessionResponse)
async def start_session_route(
    payload: FocusStartRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> FocusSessionResponse:
    focus_session = await start_session(
        db,
        user_id=user.id,
        task_id=payload.task_id,
        planned_duration_min=payload.planned_duration_min or DEFAULT_DURATION_MIN,
    )
    return FocusSessionResponse.model_validate(focus_session)


@router.post("/sessions/complete", response_model=FocusSessionResponse)
async def complete_session_route(
    payload: FocusCompleteRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> FocusSessionResponse:
    focus_session = await complete_session(db, user_id=user.id, outcome_note=payload.outcome_note)
    if focus_session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет активной фокус-сессии")
    return FocusSessionResponse.model_validate(focus_session)


@router.post("/sessions/abandon", response_model=FocusSessionResponse)
async def abandon_session_route(
    payload: FocusAbandonRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> FocusSessionResponse:
    focus_session = await abandon_session(
        db, user_id=user.id, interruption_reason=payload.interruption_reason
    )
    if focus_session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет активной фокус-сессии")
    return FocusSessionResponse.model_validate(focus_session)
