"""Backfill life_goals.target_date for rows predating it

0023 added target_date as a plain nullable column with no backfill - any
relative-horizon goal created between 0013 (life_goals existed) and 0023
(target_date added) still has target_date=NULL. carry_forward_life_goals
(app/core/life_goals/service.py) requires target_date IS NOT NULL, so
those goals can never be picked up by it: no automatic rollover, no
expiry check-in message, ever - a silently frozen deadline that only
self-heals if the user happens to open and re-save the goal manually.

Recomputes target_date_for_horizon anchored to each goal's own created_at
date (not "today", when this migration happens to run) - duplicated here
rather than imported from app.core.life_goals.service, since a migration
must stay correct at the revision it was written for, independent of that
module's logic changing later.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-29

"""
import calendar
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

WEEK_HORIZON = 0
YEAR_SCALE_MONTHS = 12


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _months_after(year: int, month: int, months_ahead: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + months_ahead
    return total // 12, total % 12 + 1


def _target_date_for_horizon(horizon_months: int, anchor: date) -> date:
    if horizon_months == WEEK_HORIZON:
        return anchor + timedelta(days=6 - anchor.weekday())
    if horizon_months >= YEAR_SCALE_MONTHS:
        years = horizon_months // 12
        return date(anchor.year + years - 1, 12, 31)
    year, month = _months_after(anchor.year, anchor.month, horizon_months - 1)
    return _end_of_month(year, month)


def upgrade() -> None:
    bind = op.get_bind()
    life_goals = sa.table(
        "life_goals",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("horizon_months", sa.Integer),
        sa.column("target_date", sa.Date),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    rows = bind.execute(
        sa.select(life_goals.c.id, life_goals.c.horizon_months, life_goals.c.created_at).where(
            life_goals.c.target_date.is_(None),
            life_goals.c.horizon_months.is_not(None),
        )
    ).all()

    for row in rows:
        target = _target_date_for_horizon(row.horizon_months, row.created_at.date())
        bind.execute(
            life_goals.update().where(life_goals.c.id == row.id).values(target_date=target)
        )


def downgrade() -> None:
    # Backfill-only, same as 0027's message_count/last_active_at - a NULL
    # target_date is indistinguishable from one the user genuinely never
    # had computed, and reverting would just re-break carry_forward_life_
    # goals for these rows again.
    pass
