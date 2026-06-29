"""Row-Level Security on the jobs table (Postgres only).

Defense-in-depth tenant isolation (app-layer ``org_id`` filtering is the primary guard). On Postgres
this enables RLS on ``jobs`` with a policy keyed on the ``app.current_org`` GUC, which the app sets
per transaction via ``db.base.set_tenant``. SQLite (dev) has no RLS, so this is a no-op there.

PROD REQUIREMENTS for RLS to actually bite: the app must connect as a NON-OWNER role (table owners
bypass RLS) and call ``set_tenant(session, org_id)`` at the start of each tenant transaction.

Revision ID: 0002_rls_policies
Revises: 4e7f73db858e
"""

from __future__ import annotations

from alembic import op

revision = "0002_rls_policies"
down_revision = "4e7f73db858e"
branch_labels = None
depends_on = None

_TABLE = "jobs"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
            USING (org_id = current_setting('app.current_org', true))
            WITH CHECK (org_id = current_setting('app.current_org', true))
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
