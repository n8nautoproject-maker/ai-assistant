"""Unique (user_id, lower(name)) index on contacts and projects

create_contact/create_project both do an app-level "find by name, else
insert" with no database guarantee behind it - two near-simultaneous calls
for the same name (a duplicate Telegram update delivery is the realistic
trigger, see app/core/memory/service.py's own comment on the identical
class of race) both read "not found" before either commits, and both
insert. Nothing before this migration made that impossible, only less
likely - see app/core/crm/service.py::create_contact and
app/core/planner/service.py::create_project.

A plain unique index can't be added directly: any name collision already
sitting in the table would make the migration itself fail. Duplicates are
merged into the oldest row first (matching find_contact_by_name/
find_project_by_name's own tie-break), re-pointing their child rows
(contact_interactions) before the now-empty duplicates are deleted.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-29

"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _dedupe(bind, table: str, extra_child_updates: list[tuple[str, str]]) -> None:
    """extra_child_updates: list of (child_table, fk_column) to repoint at
    the surviving row before the losing duplicate rows are deleted."""
    dupes = bind.execute(
        sa.text(
            f"""
            SELECT user_id, lower(name) AS lname, array_agg(id ORDER BY created_at) AS ids
            FROM {table}
            GROUP BY user_id, lower(name)
            HAVING count(*) > 1
            """
        )
    ).all()
    for row in dupes:
        keep_id = row.ids[0]
        losing_ids = row.ids[1:]
        for child_table, fk_column in extra_child_updates:
            bind.execute(
                sa.text(
                    f"UPDATE {child_table} SET {fk_column} = :keep "
                    f"WHERE {fk_column} = ANY(:losing::uuid[])"
                ),
                {"keep": keep_id, "losing": losing_ids},
            )
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE id = ANY(:losing::uuid[])"), {"losing": losing_ids}
        )


def upgrade() -> None:
    bind = op.get_bind()
    _dedupe(bind, "contacts", [("contact_interactions", "contact_id")])
    _dedupe(bind, "projects", [("tasks", "project_id")])

    op.execute("CREATE UNIQUE INDEX uq_contacts_user_id_lower_name ON contacts (user_id, lower(name))")
    op.execute("CREATE UNIQUE INDEX uq_projects_user_id_lower_name ON projects (user_id, lower(name))")


def downgrade() -> None:
    op.execute("DROP INDEX uq_projects_user_id_lower_name")
    op.execute("DROP INDEX uq_contacts_user_id_lower_name")
