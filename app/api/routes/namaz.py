from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NamazEnableRequest, NamazStatusResponse
from app.auth.dependencies import require_active_subscription
from app.core.namaz.service import (
    PRAYER_NAMES_RU,
    disable_namaz_reminders,
    enable_namaz_reminders,
    get_upcoming_prayers,
)
from app.database.models.user import User
from app.database.session import get_db
from app.integrations.aladhan import AladhanAPIError

router = APIRouter(prefix="/namaz", tags=["namaz"])


@router.get("", response_model=NamazStatusResponse)
async def get_namaz_status(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> NamazStatusResponse:
    # No dedicated pure getter exists in the service layer - the LLM tool
    # handler (_handle_get_next_namaz, tools_registry.py) does this same
    # user.namaz_city check + get_upcoming_prayers call inline, mirrored here.
    if not user.namaz_city:
        return NamazStatusResponse(enabled=False)
    prayers = await get_upcoming_prayers(db, user.id)
    if not prayers:
        return NamazStatusResponse(enabled=True, city=user.namaz_city, country=user.namaz_country)
    next_prayer = prayers[0]
    return NamazStatusResponse(
        enabled=True,
        city=user.namaz_city,
        country=user.namaz_country,
        next_prayer_name=PRAYER_NAMES_RU.get(next_prayer.payload.get("prayer", ""), None),
        next_prayer_time=next_prayer.next_run_at,
    )


@router.post("/enable", response_model=NamazStatusResponse)
async def enable_namaz(
    payload: NamazEnableRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> NamazStatusResponse:
    try:
        await enable_namaz_reminders(db, user, payload.city, payload.country)
    except AladhanAPIError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Не получилось получить расписание намаза для этого города - попробуй другое написание.",
        ) from exc
    return await get_namaz_status(user=user, db=db)


@router.post("/disable", response_model=NamazStatusResponse)
async def disable_namaz(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> NamazStatusResponse:
    await disable_namaz_reminders(db, user)
    return NamazStatusResponse(enabled=False)
