"""namaz reminders: users.namaz_city, users.namaz_country

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23

"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("namaz_city", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("namaz_country", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "namaz_country")
    op.drop_column("users", "namaz_city")
