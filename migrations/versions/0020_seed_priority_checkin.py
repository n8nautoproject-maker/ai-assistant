"""Backfill the priority-checkin scheduled jobs (13:00 and 17:00) for users
created before this feature existed - app/auth/service.py only seeds them
for new signups (see PRIORITY_CHECKIN_HOURS there).

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-29

"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

PRIORITY_CHECKIN_HOURS = (13, 17)


def _next_daily_at(now_utc: datetime, tz_name: str, hour: int) -> datetime:
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    candidate_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate_local <= now_local:
        candidate_local += timedelta(days=1)
    return candidate_local.astimezone(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users", sa.column("id", postgresql.UUID), sa.column("timezone", sa.String)
    )
    scheduled_jobs = sa.table(
        "scheduled_jobs",
        sa.column("id", postgresql.UUID),
        sa.column("user_id", postgresql.UUID),
        sa.column("job_type", sa.String),
        sa.column("next_run_at", sa.DateTime(timezone=True)),
        sa.column("recurrence", sa.String),
        sa.column("payload", postgresql.JSONB),
        sa.column("is_active", sa.Boolean),
    )

    existing_user_ids = {
        row.user_id
        for row in bind.execute(
            sa.select(scheduled_jobs.c.user_id).where(scheduled_jobs.c.job_type == "priority_checkin")
        )
    }

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": row.id,
            "job_type": "priority_checkin",
            "next_run_at": _next_daily_at(now, row.timezone, hour),
            "recurrence": "daily",
            "payload": {},
            "is_active": True,
        }
        for row in bind.execute(sa.select(users.c.id, users.c.timezone))
        if row.id not in existing_user_ids
        for hour in PRIORITY_CHECKIN_HOURS
    ]
    if rows:
        op.bulk_insert(scheduled_jobs, rows)


def downgrade() -> None:
    # No safe downgrade - same reasoning as 0015/0016: app/auth/service.py
    # has been seeding this job_type for every new signup since this
    # migration landed, with no marker to tell those rows apart from this
    # migration's own backfill at downgrade time.
    pass
