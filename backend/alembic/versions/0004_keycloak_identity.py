"""Keycloak identity linkage: users.idp_sub, nullable password_hash, service_accounts table.

- ``users.idp_sub`` — the OIDC subject (Keycloak user id), set on first OIDC login (JIT
  provisioning or verified-email link). Unique so one IdP identity maps to exactly one user.
- ``users.password_hash`` becomes nullable — IdP-only identities carry no local password.
- ``service_accounts`` — org-scoped authorization for OAuth2 client-credentials callers
  (machine-to-machine). Mirrors the RLS posture of other org-owned tables on Postgres.

Revision ID: 0004_keycloak_identity
Revises: 3cc5d6e3a60d
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_keycloak_identity"
down_revision = "3cc5d6e3a60d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table: SQLite (dev) cannot ALTER COLUMN in place.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("idp_sub", sa.String(64), nullable=True))
        batch.alter_column("password_hash", existing_type=sa.String(512), nullable=True)
    op.create_index("ix_users_idp_sub", "users", ["idp_sub"], unique=True)

    op.create_table(
        "service_accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_service_accounts_org_id", "service_accounts", ["org_id"])
    op.create_index("ix_service_accounts_client_id", "service_accounts", ["client_id"], unique=True)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE service_accounts FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY service_accounts_tenant_isolation ON service_accounts
                USING (org_id = current_setting('app.current_org', true))
                WITH CHECK (org_id = current_setting('app.current_org', true))
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS service_accounts_tenant_isolation ON service_accounts")
        op.execute("ALTER TABLE service_accounts DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_service_accounts_client_id", "service_accounts")
    op.drop_index("ix_service_accounts_org_id", "service_accounts")
    op.drop_table("service_accounts")

    op.drop_index("ix_users_idp_sub", "users")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.String(512), nullable=False)
        batch.drop_column("idp_sub")
