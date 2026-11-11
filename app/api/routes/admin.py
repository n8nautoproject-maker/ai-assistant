from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AdminUserResponse
from app.auth.dependencies import require_admin
from app.database.models.user import User
from app.database.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[AdminUserResponse]:
    # message_count/last_active_at are persistent columns on User itself
    # (see app/database/models/user.py) - conversation_messages is a
    # rolling window that gets pruned, COUNT(*) on it would undercount
    # anyone whose older messages already got folded into memory_summary.
    users = (
        (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    )
    return [
        AdminUserResponse(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            created_at=user.created_at,
            message_count=user.message_count,
            last_active_at=user.last_active_at,
        )
        for user in users
    ]
