"""life balance wheel: life_balance_ratings

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-23

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "life_balance_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ratings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_life_balance_ratings_user_id", "life_balance_ratings", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_life_balance_ratings_user_id", table_name="life_balance_ratings")
    op.drop_table("life_balance_ratings")
