import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    TaskCreateRequest,
    TaskPriorityRequest,
    TaskResponse,
    TaskUpdateRequest,
)
from app.auth.dependencies import require_active_subscription
from app.core.tasks.service import (
    complete_task,
    create_task,
    delete_task,
    list_tasks,
    mark_priority_tasks,
    unmark_priority_task,
    update_task,
)
from app.database.models.user import User
from app.database.session import get_db

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Mini App buttons always know the real task_id (no title typed by a human
# to disambiguate) - these routes pass task_id straight through and 404 on
# a miss, unlike the LLM tool handlers in tools_registry.py which also
# accept a title and can return several candidates to disambiguate.


@router.get("", response_model=list[TaskResponse])
async def get_tasks(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    tasks = await list_tasks(db, user.id)
    return [TaskResponse.model_validate(t) for t in tasks]


@router.post("", response_model=TaskResponse)
async def post_task(
    payload: TaskCreateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    task, _was_created = await create_task(
        db,
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        project_id=payload.project_id,
    )
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def patch_task(
    task_id: uuid.UUID,
    payload: TaskUpdateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    # model_fields_set (not the field's value) distinguishes "client sent
    # due_at: null" (clear it) from "client didn't send due_at at all"
    # (leave unchanged) - update_task takes Ellipsis as its own "not
    # provided" sentinel for exactly this reason. Real incident 2026-08-21:
    # clearing a task's date from the Mini App silently did nothing before
    # this, since payload.due_at is None either way and the old code had
    # no way to tell the two cases apart.
    fields = payload.model_fields_set
    task, _candidates = await update_task(
        db,
        user_id=user.id,
        task_id=task_id,
        new_title=payload.title,
        due_at=payload.due_at if "due_at" in fields else ...,
        remind_at=payload.remind_at if "remind_at" in fields else ...,
        description=payload.description,
    )
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return TaskResponse.model_validate(task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task_route(
    task_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    task, _candidates = await complete_task(db, user_id=user.id, task_id=task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task_route(
    task_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> None:
    task, _candidates = await delete_task(db, user_id=user.id, task_id=task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")


@router.post("/priority", response_model=list[TaskResponse])
async def set_priority_tasks(
    payload: TaskPriorityRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[TaskResponse]:
    resolved, _unresolved = await mark_priority_tasks(db, user_id=user.id, task_ids=payload.task_ids)
    return [TaskResponse.model_validate(t) for t in resolved]


@router.delete("/{task_id}/priority", response_model=TaskResponse)
async def unset_priority_task(
    task_id: uuid.UUID,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    task = await unmark_priority_task(db, user_id=user.id, task_id=task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return TaskResponse.model_validate(task)
