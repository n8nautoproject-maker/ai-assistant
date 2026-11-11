"""tasks.google_event_id: links a task to its auto-synced calendar event

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-27

"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("google_event_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "google_event_id")
