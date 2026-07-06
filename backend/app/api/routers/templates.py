"""Saved mapping-template endpoints (auth-gated, org-scoped).

A user who maps a file by hand can save that mapping as a named template; on the next import of the
same export shape the picker offers it and the auto-mapper applies it. Built-in templates (e.g. the
bundled Shopify one) are returned alongside the org's own, flagged ``builtin``.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.core.errors import InvalidEntityType, NotFound
from app.pipeline.entities import EntityType
from app.pipeline.mapping.templates import TEMPLATES
from app.schemas.templates import SaveTemplateRequest, TemplateResponse
from app.services import template_service as tsvc

router = APIRouter(prefix="/templates", tags=["templates"])

# Which entity each bundled built-in template targets (its fields are voucher-shaped).
_BUILTIN_ENTITY = {"shopify": EntityType.VOUCHER}


def _entity(value: str) -> EntityType:
    try:
        return EntityType(value)
    except ValueError as exc:
        raise InvalidEntityType(
            f"Unknown entity type '{value}'.", detail=f"Valid: {[e.value for e in EntityType]}"
        ) from exc


def _builtins_for(entity: EntityType) -> list[TemplateResponse]:
    out: list[TemplateResponse] = []
    for name, tmpl in TEMPLATES.items():
        if _BUILTIN_ENTITY.get(name) != entity:
            continue
        out.append(
            TemplateResponse(
                id=f"builtin:{name}",
                name=name,
                entity_type=entity.value,
                mapping={k: v for k, v in tmpl.mapping.items() if v},
                constants=dict(tmpl.constants),
                source_columns=list(tmpl.mapping.values()),
                builtin=True,
            )
        )
    return out


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    entity_type: str,
    principal: Principal = Depends(get_principal),
) -> list[TemplateResponse]:
    entity = _entity(entity_type)

    def _op() -> list[TemplateResponse]:
        saved = [TemplateResponse(**asdict(t)) for t in tsvc.list_templates(principal.org_id, entity)]
        return _builtins_for(entity) + saved

    return await run_in_threadpool(_op)


@router.post("", response_model=TemplateResponse, status_code=201)
async def save_template(
    body: SaveTemplateRequest,
    principal: Principal = Depends(get_principal),
) -> TemplateResponse:
    entity = _entity(body.entity_type)

    def _op() -> TemplateResponse:
        view = tsvc.save_template(
            org_id=principal.org_id,
            user_id=principal.user_id,
            entity_type=entity,
            name=body.name.strip(),
            mapping=body.mapping,
            constants=body.constants,
            source_columns=body.source_columns,
        )
        return TemplateResponse(**asdict(view))

    return await run_in_threadpool(_op)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    principal: Principal = Depends(get_principal),
) -> None:
    def _op() -> None:
        if not tsvc.delete_template(principal.org_id, template_id):
            raise NotFound("No such template.")

    await run_in_threadpool(_op)
