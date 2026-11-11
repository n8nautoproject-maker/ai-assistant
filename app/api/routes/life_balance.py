from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LifeBalanceRatingRequest, LifeBalanceSnapshotResponse
from app.auth.dependencies import require_active_subscription
from app.core.life_balance.service import get_recent_ratings, save_rating
from app.database.models.user import User
from app.database.session import get_db

# Unlike the LLM tool (save_life_balance_rating/get_life_balance_history in
# tools_registry.py), this returns raw structured snapshots, not the
# preformatted Russian summary text - the Mini App renders its own list/bars,
# the chat still needs the text version for a conversational reply.
router = APIRouter(prefix="/life-balance", tags=["life-balance"])


@router.get("", response_model=list[LifeBalanceSnapshotResponse])
async def get_life_balance(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[LifeBalanceSnapshotResponse]:
    ratings = await get_recent_ratings(db, user.id, limit=2)
    return [LifeBalanceSnapshotResponse.model_validate(r) for r in ratings]


@router.post("", response_model=LifeBalanceSnapshotResponse)
async def create_life_balance_rating(
    payload: LifeBalanceRatingRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> LifeBalanceSnapshotResponse:
    rating = await save_rating(db, user.id, payload.model_dump())
    return LifeBalanceSnapshotResponse.model_validate(rating)
