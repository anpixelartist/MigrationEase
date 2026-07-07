"""Remove RLS from service_accounts — it is a CROSS-TENANT auth-lookup table.

``service_accounts`` maps a Keycloak ``clientId`` (azp) to the org it may act in. Authentication
must read it *before* the tenant is known (that is how the tenant is discovered), so a tenant-keyed
RLS policy is architecturally wrong: under the prod non-owner ``tally_app`` role (FORCE RLS),
``resolve_service_account`` returned 0 rows and ``register_service_account`` INSERTs failed the
WITH CHECK — i.e. M2M auth was completely broken in prod (masked by SQLite tests, which ignore RLS).

Tenant safety is unchanged: the table is only ever queried by unique ``client_id`` during auth and by
``org_id`` at the app layer for listing — the same model as the ``users``/``organizations`` tables,
which are likewise not tenant-scoped. Introduced by migration 0004.

Revision ID: 0006_service_accounts_no_rls
Revises: 0005_mapping_templates
"""

from __future__ import annotations

from alembic import op

revision = "0006_service_accounts_no_rls"
down_revision = "0005_mapping_templates"
branch_labels = None
depends_on = None

_TABLE = "service_accounts"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
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
