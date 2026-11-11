"""users.message_count and users.last_active_at

conversation_messages is a rolling window, not full history - anything
older than SUMMARIZE_THRESHOLD (30) gets folded into memory_summary and
DELETED (see app/core/memory/service.py:maybe_summarize), so COUNT(*) on
it badly undercounts activity for any long-running user. message_count is
a persistent counter, incremented in append_message() and immune to that
pruning. last_active_at is touched on every authenticated request (Mini
App included, not just chat) - conversation_messages only ever saw chat
turns, so it couldn't answer "is this user active" for someone who only
uses Tasks/Focus/Life Goals via the Mini App's plain REST calls.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-14

"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("message_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "users", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Best available backfill, not a true lifetime count - rows already
    # deleted by prior summarization runs can't be recovered. Starts
    # everyone from whatever's currently still in the rolling window.
    op.execute(
        """
        UPDATE users
        SET message_count = sub.count
        FROM (
            SELECT user_id, COUNT(*) AS count
            FROM conversation_messages
            WHERE role = 'user'
            GROUP BY user_id
        ) AS sub
        WHERE users.id = sub.user_id
        """
    )


def downgrade() -> None:
    op.drop_column("users", "last_active_at")
    op.drop_column("users", "message_count")
