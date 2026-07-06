"""ORM models: organizations, users, memberships, and persisted job records.

Every tenant-owned row carries ``org_id`` (indexed) — tenant isolation is enforced at the repository
layer today; Postgres RLS policies on these tables are the prod backstop (plan §9).
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # NULL for identities that only authenticate via the IdP (Keycloak) — they have no local password.
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # OIDC subject (Keycloak user id). Set on first OIDC login (JIT provision or verified-email link).
    idp_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | admin | member


class ServiceAccount(Base):
    """Org-scoped authorization for an OAuth2 client-credentials caller (machine-to-machine).

    Tenant safety: a client-credentials token resolves to an org ONLY through this table, and rows
    are created exclusively by an owner/admin of that org — the token's own claims are never
    trusted for tenancy. ``user_id`` points at a synthetic ``users`` row so downstream FKs
    (e.g. ``jobs.created_by``) work unchanged.
    """

    __tablename__ = "service_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # Keycloak clientId (azp)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin | member
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Bridge(Base):
    __tablename__ = "bridges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    api_key_hash: Mapped[str] = mapped_column(String(128), index=True)  # sha256 of the key
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    last_seen_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_org_status", "org_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="created")
    filename: Mapped[str | None] = mapped_column(String(400), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # durable artefacts — blobs live in object storage; only their keys are in the DB
    upload_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    constants_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolution_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    xml_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    push_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class MappingTemplate(Base):
    """A reusable, org-scoped column mapping a user saved from a manual mapping.

    Feeds two places: the template picker on the Upload step, and the auto-mapper —
    ``suggest_mapping`` overlays the best-matching saved template (by source-column overlap) so a
    repeat import of the same export shape auto-maps with high confidence instead of re-doing the
    manual mapping. Uniqueness is per ``(org_id, entity_type, name)`` so a name can be reused across
    entity types and saving the same name upserts.
    """

    __tablename__ = "mapping_templates"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_type", "name", name="uq_mapping_template_org_entity_name"),
        Index("ix_mapping_templates_org_entity", "org_id", "entity_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    # {canonical_target_field: source_column} — the accepted column mapping.
    mapping_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # {canonical_target_field: fixed_value} — the "set a fixed value" constants.
    constants_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # the source columns present in the file when saved — used to score auto-match against new files.
    source_columns_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class StagedRecord(Base):
    __tablename__ = "staged_records"
    __table_args__ = (
        Index("ix_staged_records_job_order", "job_id", "order_id"),
        Index("ix_staged_records_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
