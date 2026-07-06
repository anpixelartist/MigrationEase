"""Saved mapping templates (org-scoped, reusable column mappings).

Adds the ``mapping_templates`` table plus, on Postgres, the same RLS tenant-isolation policy the
other tenant-owned tables carry (keyed on the ``app.current_org`` GUC; no-op on SQLite).

Revision ID: 0005_mapping_templates
Revises: 0004_keycloak_identity
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_mapping_templates"
down_revision = "0004_keycloak_identity"
branch_labels = None
depends_on = None

_TABLE = "mapping_templates"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("mapping_json", sa.JSON(), nullable=False),
        sa.Column("constants_json", sa.JSON(), nullable=False),
        sa.Column("source_columns_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "entity_type", "name", name="uq_mapping_template_org_entity_name"),
    )
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_mapping_templates_org_id"), ["org_id"], unique=False)
        batch_op.create_index("ix_mapping_templates_org_entity", ["org_id", "entity_type"], unique=False)

    # Postgres RLS backstop (mirrors 0002_rls_policies for the jobs table). No-op on SQLite.
    if op.get_bind().dialect.name == "postgresql":
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
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_index("ix_mapping_templates_org_entity")
        batch_op.drop_index(batch_op.f("ix_mapping_templates_org_id"))
    op.drop_table(_TABLE)
