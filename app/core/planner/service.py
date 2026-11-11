import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.project import Project
from app.database.models.task import Task, TaskStatus

# Ordering shared by get_daily_plan and the morning check-in's top-task pick
# (app/scheduler/handlers.py::_pick_top_task): earliest of due_at/remind_at
# first, tasks with neither fall back to most recently created. Using only
# due_at here used to push remind_at-only tasks (e.g. "напомни выехать в
# 09:15", which has no due_at) to the back of the list, where a plan_day
# limit could cut them off entirely - see the "Выехать к Роме" bug.
_priority_moment = func.coalesce(Task.due_at, Task.remind_at)

DAILY_PLAN_LIMIT = 5


async def find_project_by_name(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> Project | None:
    result = await session.execute(
        select(Project)
        .where(Project.user_id == user_id, func.lower(Project.name) == name.lower())
        # Deterministic tie-break for same-name projects, same reasoning as
        # crm_service.find_contact_by_name's identical fix.
        .order_by(Project.created_at)
    )
    return result.scalars().first()


async def create_project(
    session: AsyncSession, user_id: uuid.UUID, name: str, description: str | None = None
) -> tuple[Project, bool]:
    """Returns (project, was_created). Reuses an existing project with the
    same name instead of always inserting a new row - same dedup fix
    already applied to crm_service.create_contact for the identical bug: a
    repeated call for the same name (an LLM retry, a restated request)
    used to create a second "ghost" Project row, and find_project_by_name's
    unordered .first() (used by get_or_create_project when create_task
    later attaches to a project by name) could then non-deterministically
    resolve to either one.
    """
    existing = await find_project_by_name(session, user_id, name)
    if existing is not None:
        existing.description = existing.description or description
        await session.commit()
        await session.refresh(existing)
        return existing, False

    project = Project(user_id=user_id, name=name, description=description)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race against a concurrent create_project for the same
        # name - the unique index on (user_id, lower(name)) is what
        # actually makes the duplicate impossible now; this just means the
        # loser merges into the winner instead of crashing with a raw 500.
        await session.rollback()
        winner = await find_project_by_name(session, user_id, name)
        assert winner is not None  # the row that just violated our own insert must exist
        winner.description = winner.description or description
        await session.commit()
        await session.refresh(winner)
        return winner, False
    await session.refresh(project)
    return project, True


async def get_or_create_project(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> Project:
    project = await find_project_by_name(session, user_id, name)
    if project is not None:
        return project
    project, _was_created = await create_project(session, user_id, name)
    return project


async def list_projects(session: AsyncSession, user_id: uuid.UUID) -> list[Project]:
    result = await session.execute(
        select(Project).where(Project.user_id == user_id).order_by(Project.name)
    )
    return list(result.scalars().all())


async def get_daily_plan(
    session: AsyncSession, user_id: uuid.UUID, tz_name: str, limit: int = DAILY_PLAN_LIMIT
) -> list[Task]:
    """Real bug found live: this used to just return the top N pending
    tasks by soonest due_at/remind_at with no day filter at all - if the
    nearest-due tasks happened to be tomorrow (or later), "план на день"
    showed those as today's plan. Now only tasks due/reminded *today* (in
    the user's own timezone) or with no moment at all (undated candidates)
    are eligible - a task due tomorrow no longer leaks into today's plan.
    """
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day_local = start_of_day_local + timedelta(days=1)

    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status == TaskStatus.PENDING.value,
            (_priority_moment.is_(None))
            | (
                (_priority_moment >= start_of_day_local)
                & (_priority_moment < end_of_day_local)
            ),
        )
        .order_by(_priority_moment.is_(None), _priority_moment.asc(), Task.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
