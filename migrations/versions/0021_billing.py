"""users.yookassa_payment_method_id + payments table

Groundwork for real ЮKassa billing - see app/core/billing/service.py.
payments is an audit trail (what was charged, ЮKassa's payment id, status)
- subscription state itself lives on users (subscription_status,
current_period_end), unchanged by this migration.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-02

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("yookassa_payment_method_id", sa.String(length=64), nullable=True)
    )
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("yookassa_payment_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_yookassa_payment_id", "payments", ["yookassa_payment_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_yookassa_payment_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_column("users", "yookassa_payment_method_id")
