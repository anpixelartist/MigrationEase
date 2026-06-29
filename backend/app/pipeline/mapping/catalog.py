"""Load the canonical Tally field catalog (shared/canonical_fields/*.json) into typed models.

The catalog is the single contract the auto-mapper, validator, and XML builder all read. It lives
outside the backend (language-neutral JSON) so the frontend can consume it too.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.pipeline.entities import EntityType


class CanonicalField(BaseModel):
    key: str
    label: str
    tally_tag: str | None = None
    dtype: str
    required: bool = False
    unique: bool = False
    synonyms: list[str] = []
    value_set_ref: str | None = None
    regex: str | None = None
    example: str | None = None
    enum_values: list[str] | None = None


class CanonicalCatalog(BaseModel):
    entity: str
    schema_version: int
    tally_element: str
    create_order: int | None = None
    fields: list[CanonicalField]

    def by_key(self, key: str) -> CanonicalField | None:
        return next((f for f in self.fields if f.key == key), None)


def _find_catalog_dir() -> Path:
    """Walk up from this file to find ``shared/canonical_fields`` (works from any checkout layout)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "shared" / "canonical_fields"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not locate shared/canonical_fields relative to the backend.")


@lru_cache(maxsize=None)
def load_catalog(entity: EntityType) -> CanonicalCatalog:
    path = _find_catalog_dir() / f"{entity.value}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return CanonicalCatalog(**data)
