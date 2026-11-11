from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_access_token
from app.config import get_settings
from app.database.models.user import User
from app.database.session import get_db
from app.subscriptions.gate import SubscriptionRequired, check_subscription

bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        user_id = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        ) from exc

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")

    # Every authenticated Mini App request, not just chat turns - see
    # app/auth/service.py:get_or_create_user for the bot-message-side touch.
    user.last_active_at = datetime.now(timezone.utc)
    await db.commit()
    return user


async def require_active_subscription(user: User = Depends(get_current_user)) -> User:
    """Gates paid/metered actions (agent calls, proactive jobs).

    MVP enforcement: trialing or active only. Plan-specific quotas
    (tokens/day, message count) are not tracked yet - this only checks
    that the subscription itself is in good standing.
    """
    try:
        check_subscription(user)
    except SubscriptionRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
        ) from exc
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gates the /admin/* routes - a single hardcoded owner id (ADMIN_TELEGRAM_ID),
    not a role/permission system - there's exactly one admin, not a general concept."""
    settings = get_settings()
    if settings.admin_telegram_id == 0 or user.telegram_id != settings.admin_telegram_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user
