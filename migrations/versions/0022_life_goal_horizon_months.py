"""life_goals.horizon_years -> horizon_months

Switches the stored unit from whole years to whole months, so the fixed
horizon set can include sub-year buckets (1 month, 3 months, half a year)
without resorting to fractional years. Existing rows are converted
(years * 12) before the rename so their real-world meaning is preserved.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-05

"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE life_goals SET horizon_years = horizon_years * 12 WHERE horizon_years IS NOT NULL")
    op.alter_column("life_goals", "horizon_years", new_column_name="horizon_months")


def downgrade() -> None:
    op.alter_column("life_goals", "horizon_months", new_column_name="horizon_years")
    # Integer division truncates: any sub-year horizon (WEEK_HORIZON=0, or
    # 1/3/6 months - all added after this migration, so real production
    # rows can genuinely have these) silently became 0 years, indistin-
    # guishable from a data-entry error in the old whole-years schema.
    # Rounding UP to the nearest whole year instead is still lossy (the old
    # schema has no way to represent "1 month"), but "1 year" is at least a
    # sane, non-zero approximation instead of quietly erasing the horizon.
    op.execute(
        "UPDATE life_goals SET horizon_years = GREATEST(CEIL(horizon_years::numeric / 12)::integer, 1) "
        "WHERE horizon_years IS NOT NULL"
    )
