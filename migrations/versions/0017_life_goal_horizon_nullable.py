"""life_goals.horizon_years nullable: NULL = "Задачи на жизнь" (no year horizon)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-27

"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("life_goals", "horizon_years", nullable=True)


def downgrade() -> None:
    op.alter_column("life_goals", "horizon_years", nullable=False)
