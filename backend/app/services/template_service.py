"""Saved mapping templates — org-scoped CRUD + best-match selection for the auto-mapper.

A template stores an accepted ``{target_field: source_column}`` mapping plus ``constants`` and the
source columns present when it was saved. On a later import the auto-mapper (``suggest_mapping``)
overlays the best-matching template — measured by how many of its source columns are present in the
new file — so repeat imports of the same export shape auto-map without re-doing the manual work.

Tenant isolation: every query is filtered by ``org_id`` at the app layer AND runs ``set_tenant`` so
the Postgres RLS policy (migration 0005) bites when the app connects as the non-owner ``tally_app``
role. (Mirrors ``job_repo``; contrast ``service_accounts``, which is a cross-tenant auth lookup.)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.base import new_session, set_tenant
from app.db.models import MappingTemplate
from app.pipeline.entities import EntityType


@dataclass(frozen=True)
class TemplateView:
    id: str
    name: str
    entity_type: str
    mapping: dict[str, str]
    constants: dict[str, str]
    source_columns: list[str]
    builtin: bool
    created_at: str | None


def _view(t: MappingTemplate) -> TemplateView:
    return TemplateView(
        id=t.id,
        name=t.name,
        entity_type=t.entity_type,
        mapping=dict(t.mapping_json or {}),
        constants=dict(t.constants_json or {}),
        source_columns=list(t.source_columns_json or []),
        builtin=False,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


def list_templates(org_id: str, entity_type: EntityType) -> list[TemplateView]:
    with new_session() as db:
        set_tenant(db, org_id)
        rows = (
            db.query(MappingTemplate)
            .filter(MappingTemplate.org_id == org_id, MappingTemplate.entity_type == entity_type.value)
            .order_by(MappingTemplate.name)
            .all()
        )
        return [_view(t) for t in rows]


def save_template(
    org_id: str,
    user_id: str,
    entity_type: EntityType,
    name: str,
    mapping: dict[str, str | None],
    constants: dict[str, str],
    source_columns: list[str],
) -> TemplateView:
    """Create or overwrite (upsert by org+entity+name) a saved template. Only accepted column
    mappings are stored (``None``/empty source columns are dropped)."""
    clean_mapping = {t: s for t, s in (mapping or {}).items() if s}
    clean_constants = {t: v for t, v in (constants or {}).items() if str(v).strip() != ""}
    with new_session() as db:
        set_tenant(db, org_id)
        existing = (
            db.query(MappingTemplate)
            .filter(
                MappingTemplate.org_id == org_id,
                MappingTemplate.entity_type == entity_type.value,
                MappingTemplate.name == name,
            )
            .one_or_none()
        )
        if existing is None:
            existing = MappingTemplate(
                org_id=org_id, created_by=user_id, entity_type=entity_type.value, name=name
            )
            db.add(existing)
        existing.mapping_json = clean_mapping
        existing.constants_json = clean_constants
        existing.source_columns_json = list(source_columns or [])
        db.commit()
        db.refresh(existing)
        return _view(existing)


def delete_template(org_id: str, template_id: str) -> bool:
    with new_session() as db:
        set_tenant(db, org_id)
        row = (
            db.query(MappingTemplate)
            .filter(MappingTemplate.org_id == org_id, MappingTemplate.id == template_id)
            .one_or_none()
        )
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


def best_match(org_id: str, entity_type: EntityType, columns: list[str]) -> TemplateView | None:
    """Pick the saved template whose source columns best overlap the uploaded file's columns.

    Score = fraction of the template's source columns present in the file. Requires a solid overlap
    (>=60% and >=2 columns) so an unrelated file never gets a stale template forced onto it.
    """
    present = {c for c in columns}
    best: TemplateView | None = None
    best_score = 0.0
    for t in list_templates(org_id, entity_type):
        cols = [c for c in t.source_columns if c]
        if len(cols) < 2:
            continue
        hits = sum(1 for c in cols if c in present)
        score = hits / len(cols)
        if score >= 0.6 and score > best_score:
            best, best_score = t, score
    return best
