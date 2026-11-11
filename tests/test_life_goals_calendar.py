"""target_date_for_horizon / next_target_date - the calendar-anchoring math
behind every life goal's deadline. Pure functions, no DB needed. This is
the exact logic migration 0028 (in this same audit) had to duplicate to
backfill legacy rows - a test here means the next change to this function
can be checked against the real behavior instead of by re-deriving it by
hand again.
"""
from datetime import date

from app.core.life_goals.service import WEEK_HORIZON, next_target_date, target_date_for_horizon


def test_week_horizon_anchors_to_end_of_iso_week():
    # Monday 2026-08-24 -> Sunday 2026-08-30
    assert target_date_for_horizon(WEEK_HORIZON, None, today=date(2026, 8, 24)) == date(2026, 8, 30)


def test_month_horizon_counts_current_month_as_month_one():
    assert target_date_for_horizon(1, None, today=date(2026, 8, 15)) == date(2026, 8, 31)


def test_three_month_horizon_rolls_into_the_following_year_correctly():
    assert target_date_for_horizon(3, None, today=date(2026, 11, 15)) == date(2027, 1, 31)


def test_year_scale_horizon_anchors_to_calendar_year_end():
    assert target_date_for_horizon(12, None, today=date(2026, 3, 1)) == date(2026, 12, 31)


def test_multi_year_horizon_counts_current_year_as_year_one():
    assert target_date_for_horizon(60, None, today=date(2026, 3, 1)) == date(2030, 12, 31)


def test_explicit_target_date_always_wins_over_horizon():
    explicit = date(2027, 5, 31)
    assert target_date_for_horizon(3, explicit, today=date(2026, 8, 15)) == explicit


def test_lifetime_horizon_has_no_calendar_anchor():
    assert target_date_for_horizon(None, None, today=date(2026, 8, 15)) is None


def test_next_target_date_week_adds_seven_days():
    assert next_target_date(WEEK_HORIZON, date(2026, 8, 30)) == date(2026, 9, 6)


def test_next_target_date_month_rolls_forward_one_month():
    assert next_target_date(1, date(2026, 8, 31)) == date(2026, 9, 30)


def test_next_target_date_year_scale_adds_one_year():
    assert next_target_date(12, date(2026, 12, 31)) == date(2027, 12, 31)


def test_next_target_date_lifetime_has_no_next():
    assert next_target_date(None, date(2026, 8, 31)) is None
