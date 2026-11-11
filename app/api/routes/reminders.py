import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IntervalReminderRequest, RecurringReminderRequest, ReminderResponse
from app.auth.dependencies import require_active_subscription
from app.core.reminders.service import (
    cancel_recurring_reminder,
    create_interval_reminder,
    create_recurring_reminder,
    first_interval_occurrence,
    list_recurring_reminders,
    next_occurrence,
)
from app.database.models.user import User
from app.database.session import get_db

# Mirrors set_recurring_reminder/set_interval_reminder/list_reminders/
# cancel_reminder in tools_registry.py - same next_occurrence/
# first_interval_occurrence date math (moved to app/core/reminders/service.py
# so it's not duplicated between the LLM tool handlers and this route).
router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.get("", response_model=list[ReminderResponse])
async def list_reminders(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[ReminderResponse]:
    reminders = await list_recurring_reminders(db, user.id)
    return [ReminderResponse.model_validate(r) for r in reminders]


@router.post("/recurring", response_model=ReminderResponse)
async def create_recurring(
    payload: RecurringReminderRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ReminderResponse:
    next_run_at = next_occurrence(payload.time, user.timezone)
    reminder = await create_recurring_reminder(
        db, user.id, text=payload.text, next_run_at=next_run_at, recurrence=payload.recurrence
    )
    return ReminderResponse.model_validate(reminder)


@router.post("/interval", response_model=ReminderResponse)
async def create_interval(
    payload: IntervalReminderRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ReminderResponse:
    next_run_at = first_interval_occurrence(
        payload.window_start, payload.window_end, payload.interval_minutes, user.timezone
    )
    reminder = await create_interval_reminder(
        db,
        user.id,
        text=payload.text,
        next_run_at=next_run_at,
        interval_minutes=payload.interval_minutes,
        window_start=payload.window_start,
        window_end=payload.window_end,
    )
    return ReminderResponse.model_validate(reminder)


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(
    reminder_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await cancel_recurring_reminder(db, user.id, reminder_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Напоминание не найдено")
