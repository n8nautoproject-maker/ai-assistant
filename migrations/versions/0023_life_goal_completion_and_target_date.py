"""life_goals: is_done + target_date

Adds a completion flag (goals had no way to be marked done at all before
this) and an explicit calendar target_date - needed so "На год"/"На N лет"
can anchor to a real calendar year-end instead of a vague N-months-out
horizon, and so week/specific-month goals have a concrete date to group
and display by.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-09

"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "life_goals",
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("life_goals", sa.Column("target_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("life_goals", "target_date")
    op.drop_column("life_goals", "is_done")
