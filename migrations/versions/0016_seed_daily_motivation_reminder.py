"""Backfill the daily 8:00 "look at today's tasks" reminder for users
created before this default existed - app/auth/service.py only seeds it
for new signups (see DAILY_MOTIVATION_TEXT there).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-27

"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

DAILY_MOTIVATION_HOUR = 8
DAILY_MOTIVATION_TEXT = (
    "Посмотрите свои задачи на сегодняшний день, поставьте новые. "
    "Пусть этот день будет максимально продуктивным!"
)


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

    # Job_type=custom_reminder is shared with user-authored reminders, so
    # "already has one" must match on this exact text, not just job_type -
    # a user's own unrelated custom_reminder shouldn't block seeding this.
    existing_user_ids = {
        row.user_id
        for row in bind.execute(
            sa.select(scheduled_jobs.c.user_id).where(
                scheduled_jobs.c.job_type == "custom_reminder",
                scheduled_jobs.c.payload["text"].astext == DAILY_MOTIVATION_TEXT,
            )
        )
    }

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": row.id,
            "job_type": "custom_reminder",
            "next_run_at": _next_daily_at(now, row.timezone, DAILY_MOTIVATION_HOUR),
            "recurrence": "daily",
            "payload": {"text": DAILY_MOTIVATION_TEXT},
            "is_active": True,
        }
        for row in bind.execute(sa.select(users.c.id, users.c.timezone))
        if row.id not in existing_user_ids
    ]
    if rows:
        op.bulk_insert(scheduled_jobs, rows)


def downgrade() -> None:
    # No safe downgrade: app/auth/service.py has been seeding this exact
    # job_type + payload text for every new signup since this migration
    # landed, and there's no marker distinguishing this migration's
    # backfilled rows from ones created by normal operation afterward. The
    # original DELETE here matched on job_type + payload text alone, which
    # would silently remove every such reminder ever seeded since, not just
    # the ones this specific migration inserted.
    pass
