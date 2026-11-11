from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ProjectCreateRequest, ProjectResponse
from app.auth.dependencies import require_active_subscription
from app.core.planner.service import create_project, list_projects
from app.database.models.user import User
from app.database.session import get_db

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
async def get_projects(
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectResponse]:
    projects = await list_projects(db, user.id)
    return [ProjectResponse.model_validate(p) for p in projects]


@router.post("", response_model=ProjectResponse)
async def post_project(
    payload: ProjectCreateRequest,
    user: User = Depends(require_active_subscription),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, _was_created = await create_project(
        db, user_id=user.id, name=payload.name, description=payload.description
    )
    return ProjectResponse.model_validate(project)
