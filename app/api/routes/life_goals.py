import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LifeGoalCreateRequest, LifeGoalResponse, LifeGoalUpdateRequest
from app.auth.dependencies import require_active_subscription
from app.core.life_goals.service import list_goals, save_goal, set_goal_done, update_goal
from app.database.models.user import User
from app.database.session import get_db

# Mirrors the LLM tools (save_life_goal/list_life_goals in tools_registry.py)
# 1:1 for create/list, same as tasks.py/focus.py already do for their
# domains. The PATCH route below has no LLM-tool equivalent yet - editing an
# existing goal's horizon is a Mini App-only affordance so far.
router = APIRouter(prefix="/life-goals", tags=["life-goals"])


@router.get("", response_model=list[LifeGoalResponse])
async def list_life_goals(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[LifeGoalResponse]:
    goals = await list_goals(db, user.id)
    return [LifeGoalResponse.model_validate(g) for g in goals]


@router.post("", response_model=LifeGoalResponse)
async def create_life_goal(
    payload: LifeGoalCreateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> LifeGoalResponse:
    goal = await save_goal(
        db,
        user.id,
        horizon_months=payload.horizon_months,
        title=payload.title,
        description=payload.description,
        target_date=payload.target_date,
    )
    return LifeGoalResponse.model_validate(goal)


@router.patch("/{goal_id}", response_model=LifeGoalResponse)
async def patch_life_goal(
    goal_id: uuid.UUID,
    payload: LifeGoalUpdateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> LifeGoalResponse:
    # model_fields_set (not payload.horizon_months's value) distinguishes
    # "client sent horizon_months: null" (clear it) from "client didn't
    # send horizon_months at all" (leave unchanged) - see update_goal's
    # docstring for why None can't double as that sentinel here.
    fields = payload.model_fields_set
    goal = await update_goal(
        db,
        user.id,
        goal_id,
        horizon_months=payload.horizon_months if "horizon_months" in fields else ...,
        title=payload.title,
        description=payload.description if "description" in fields else ...,
        target_date=payload.target_date if "target_date" in fields else ...,
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Цель не найдена")
    return LifeGoalResponse.model_validate(goal)


@router.post("/{goal_id}/complete", response_model=LifeGoalResponse)
async def complete_life_goal(
    goal_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> LifeGoalResponse:
    goal = await set_goal_done(db, user.id, goal_id, True)
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Цель не найдена")
    return LifeGoalResponse.model_validate(goal)


@router.post("/{goal_id}/uncomplete", response_model=LifeGoalResponse)
async def uncomplete_life_goal(
    goal_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> LifeGoalResponse:
    goal = await set_goal_done(db, user.id, goal_id, False)
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Цель не найдена")
    return LifeGoalResponse.model_validate(goal)
