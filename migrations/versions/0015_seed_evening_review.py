"""Backfill evening_review scheduled_jobs for users created before this
job type existed - app/auth/service.py only seeds it for new signups, so
without this data migration every existing user would silently never get
the new overdue-task check-in.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27

"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

EVENING_REVIEW_HOUR = 21


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
            sa.select(scheduled_jobs.c.user_id).where(
                scheduled_jobs.c.job_type == "evening_review"
            )
        )
    }

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": row.id,
            "job_type": "evening_review",
            "next_run_at": _next_daily_at(now, row.timezone, EVENING_REVIEW_HOUR),
            "recurrence": "daily",
            "payload": {},
            "is_active": True,
        }
        for row in bind.execute(sa.select(users.c.id, users.c.timezone))
        if row.id not in existing_user_ids
    ]
    if rows:
        op.bulk_insert(scheduled_jobs, rows)


def downgrade() -> None:
    # No safe downgrade: app/auth/service.py has been seeding this same
    # job_type for every new signup since this migration landed, and
    # there's no marker distinguishing this migration's backfilled rows
    # from rows created by normal operation afterward. The original
    # unscoped DELETE here would silently disable evening_review for every
    # user who signed up (or was backfilled) after this ran, not just the
    # ones this specific migration touched.
    pass
