"""bridges table (paired local agents).

Revision ID: 0003_bridges
Revises: 0002_rls_policies
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_bridges"
down_revision = "0002_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bridges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("api_key_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bridges_org_id", "bridges", ["org_id"])
    op.create_index("ix_bridges_api_key_hash", "bridges", ["api_key_hash"])


def downgrade() -> None:
    op.drop_index("ix_bridges_api_key_hash", "bridges")
    op.drop_index("ix_bridges_org_id", "bridges")
    op.drop_table("bridges")
