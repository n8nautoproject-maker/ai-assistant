"""scheduled_jobs: composite index on (job_type, is_active)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-28

Full-project audit finding: _deactivate_task_reminder (app/core/tasks/service.py)
and _cancel_followup_job (app/core/focus/service.py) both filter on
job_type + is_active + a JSONB payload key on every task complete/delete
and every focus-session complete/abandon - only job_type was indexed, so
each of these ran a sequential scan over every row of that job_type.
Not a correctness bug at current volume, but worth closing before it
becomes one as scheduled_jobs grows.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scheduled_jobs_job_type_is_active",
        "scheduled_jobs",
        ["job_type", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_jobs_job_type_is_active", table_name="scheduled_jobs")
