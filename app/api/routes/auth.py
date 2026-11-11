from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TelegramLoginPayload, TelegramWebAppAuthRequest, TokenResponse
from app.auth.jwt import create_access_token
from app.auth.service import get_or_create_user
from app.auth.telegram import InvalidTelegramAuth, verify_login_widget, verify_webapp_init_data
from app.config import get_settings
from app.database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram-login", response_model=TokenResponse)
async def telegram_login(
    payload: TelegramLoginPayload, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    settings = get_settings()
    try:
        verify_login_widget(payload.model_dump(exclude_none=True), settings.telegram_bot_token)
    except InvalidTelegramAuth as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    user = await get_or_create_user(
        db,
        telegram_id=payload.id,
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/telegram-webapp", response_model=TokenResponse)
async def telegram_webapp_login(
    payload: TelegramWebAppAuthRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Auth for the Mini App - takes `window.Telegram.WebApp.initData`
    verbatim, a different signing scheme from the Login Widget above."""
    settings = get_settings()
    try:
        tg_user = verify_webapp_init_data(payload.init_data, settings.telegram_bot_token)
    except InvalidTelegramAuth as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    user = await get_or_create_user(
        db,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
        last_name=tg_user.get("last_name"),
    )
    return TokenResponse(access_token=create_access_token(user.id))
