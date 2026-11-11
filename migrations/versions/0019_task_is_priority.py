"""tasks.is_priority: marks a task as user-designated priority for today

Set when the user answers the morning check-in's "какая задача сегодня в
приоритете?" question (replaces the old auto-picked single "main task").
Cleared and re-asked fresh every morning, not a persistent property of the
task.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-29

"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("is_priority", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("tasks", "is_priority")
