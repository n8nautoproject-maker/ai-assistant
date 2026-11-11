"""life_goals.carried_over

Marks a goal that was auto-rolled onto its next period by
carry_forward_life_goals after its previous target_date expired - lets the
Mini App group it under "Перенесённые с прошлого месяца" instead of
blending back into a fresh-looking bucket.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-13

"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "life_goals",
        sa.Column("carried_over", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("life_goals", "carried_over")
