"""task followup: tasks.awaiting_confirmation

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-18

"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "awaiting_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "awaiting_confirmation")
