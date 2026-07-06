"""Auto-mapping stage — propose source-column -> canonical-field mappings, ranked by confidence.

``confidence = type_mult * (w_embed*s_embed + w_fuzz*s_fuzz)`` (plan §6). Semantic embeddings
(sentence-transformers) are optional and not wired yet, so we run the **pure-fuzzy + type-gate**
fallback (``w_embed=0, w_fuzz=1``), which is exactly the cold-start path. A one-to-one assignment
(``scipy.linear_sum_assignment``) prevents two columns grabbing the same field.

Decision per winning field: ``>=0.85`` auto-accept, ``0.55-0.85`` needs-confirm, ``<0.55`` unmapped.
Required fields are always forced to at least needs-confirm, and near-ties are demoted — a human must
always eyeball the load-bearing fields (name / parent / base_units).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from app.pipeline.contracts import ColumnMapping, MappingPlan, StageName, StageResult
from app.pipeline.entities import EntityType
from app.pipeline.mapping.catalog import CanonicalField, load_catalog

__all__ = ["auto_map", "FieldSuggestion", "MappingProposal"]

AUTO_ACCEPT = 0.85
# Floor below which a field is left unmapped. Empirically, token_set_ratio gives clearly-unrelated
# headers up to ~0.56 against short synonyms (e.g. "Random Notes" vs "pan no"), while real matches
# via curated synonyms score >=0.85 — so 0.62 cleanly separates noise from signal. Sub-floor matches
# are NOT lost: each field still surfaces its top-3 `alternatives` for manual selection.
NEEDS_CONFIRM = 0.62
NEAR_TIE = 0.07

_BOOLISH = {"yes", "no", "true", "false", "1", "0", "y", "n"}


class FieldSuggestion(BaseModel):
    target_field: str
    label: str
    required: bool
    source_column: str | None
    confidence: float
    status: str  # auto_accept | needs_confirm | unmapped
    method: str  # fuzzy | embedding | template | manual
    alternatives: list[tuple[str, float]] = Field(default_factory=list)


class MappingProposal(BaseModel):
    entity: EntityType
    suggestions: list[FieldSuggestion]
    unmapped_sources: list[str] = Field(default_factory=list)
    unmapped_required: list[str] = Field(default_factory=list)
    # Set when a saved org template matched the file and was overlaid onto the suggestions above.
    applied_template: str | None = None
    # Constants carried by that template (target_field -> fixed value) for the UI to pre-fill.
    applied_constants: dict[str, str] = Field(default_factory=dict)

    def to_plan(self) -> MappingPlan:
        """A MappingPlan of the currently-assigned columns (caller confirms before use)."""
        mappings = [
            ColumnMapping(
                source_column=s.source_column,
                target_field=s.target_field,
                confidence=s.confidence,
                method="fuzzy",
            )
            for s in self.suggestions
            if s.source_column is not None
        ]
        return MappingPlan(entity=self.entity, mappings=mappings)


def _norm(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def _is_number(value: object) -> bool:
    try:
        Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return False
    return True


def _header_score(source: str, field: CanonicalField) -> float:
    """Best fuzzy match of the source header against the field label/key/synonyms (0..1)."""
    targets = [field.label, field.key, *field.synonyms]
    source_norm = _norm(source)
    best = max(fuzz.token_set_ratio(source_norm, _norm(t)) for t in targets)
    return best / 100.0


def _source_stats(series: pd.Series) -> dict[str, float]:
    total = len(series)
    values = [
        v
        for v in series.tolist()
        if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""
    ]
    n = len(values)
    if n == 0:
        return {"n": 0.0, "numeric": 0.0, "distinct": 0.0, "null": 1.0, "boolish": 0.0}
    return {
        "n": float(n),
        "numeric": sum(_is_number(v) for v in values) / n,
        "distinct": len({_norm(v) for v in values}) / n,
        "null": 1.0 - (n / total) if total else 0.0,
        "boolish": sum(_norm(v) in _BOOLISH for v in values) / n,
    }


def _type_mult(field: CanonicalField, stats: dict[str, float]) -> float:
    """Type-compatibility multiplier from profiling signals (header-only => neutral 1.0)."""
    if stats["n"] == 0:
        return 1.0
    dtype = field.dtype
    if dtype in ("amount", "decimal", "integer"):
        if stats["numeric"] >= 0.8:
            return 1.0
        if stats["numeric"] >= 0.4:
            return 0.8
        return 0.3
    if dtype == "bool":
        return 1.0 if stats["boolish"] >= 0.8 else 0.5
    if field.unique:  # identity/name fields prefer a near-unique, non-null column
        if stats["distinct"] >= 0.95 and stats["null"] <= 0.05:
            return 1.0
        if stats["distinct"] < 0.5:
            return 0.6
        return 0.9
    return 1.0


def _status(field: CanonicalField, best_conf: float, ranked_conf: list[float]) -> str:
    if best_conf >= AUTO_ACCEPT:
        status = "auto_accept"
        if len(ranked_conf) >= 2 and (ranked_conf[0] - ranked_conf[1]) < NEAR_TIE and ranked_conf[1] >= NEEDS_CONFIRM:
            status = "needs_confirm"  # too close to call -> ask
    elif best_conf >= NEEDS_CONFIRM:
        status = "needs_confirm"
    else:
        return "unmapped"
    # a human must always eyeball the load-bearing fields
    if field.required and status == "auto_accept":
        status = "needs_confirm"
    return status


def auto_map(df: pd.DataFrame, entity: EntityType) -> StageResult[MappingProposal]:
    """Propose a mapping of ``df`` columns to ``entity`` canonical fields."""
    fields = load_catalog(entity).fields
    sources = list(df.columns)
    stats = {col: _source_stats(df[col]) for col in sources}

    conf = np.zeros((len(fields), len(sources)))
    for ti, field in enumerate(fields):
        for si, col in enumerate(sources):
            conf[ti, si] = _type_mult(field, stats[col]) * _header_score(col, field)

    assignment: dict[int, int] = {}
    if fields and sources:
        rows, cols = linear_sum_assignment(-conf)  # maximize total confidence
        assignment = {int(r): int(c) for r, c in zip(rows, cols)}

    suggestions: list[FieldSuggestion] = []
    used_sources: set[int] = set()
    for ti, field in enumerate(fields):
        ranked = sorted(range(len(sources)), key=lambda si: conf[ti, si], reverse=True)
        ranked_conf = [float(conf[ti, si]) for si in ranked]
        best_si = assignment.get(ti)
        best_conf = float(conf[ti, best_si]) if best_si is not None else 0.0

        status = _status(field, best_conf, ranked_conf)
        alternatives = [
            (sources[si], round(float(conf[ti, si]), 4)) for si in ranked[:4] if si != best_si
        ][:3]

        if best_si is not None and best_conf >= NEEDS_CONFIRM:
            source_column: str | None = sources[best_si]
            used_sources.add(best_si)
        else:
            source_column = None
            status = "unmapped"

        suggestions.append(
            FieldSuggestion(
                target_field=field.key,
                label=field.label,
                required=field.required,
                source_column=source_column,
                confidence=round(best_conf, 4),
                status=status,
                method="fuzzy",
                alternatives=alternatives,
            )
        )

    unmapped_sources = [sources[si] for si in range(len(sources)) if si not in used_sources]
    unmapped_required = [
        s.target_field for s in suggestions if s.required and s.source_column is None
    ]

    proposal = MappingProposal(
        entity=entity,
        suggestions=suggestions,
        unmapped_sources=unmapped_sources,
        unmapped_required=unmapped_required,
    )
    return StageResult(
        ok=not unmapped_required,
        stage=StageName.MAP,
        data=proposal,
        errors=[],
        stats={
            "sources": len(sources),
            "auto_accepted": sum(1 for s in suggestions if s.status == "auto_accept"),
            "needs_confirm": sum(1 for s in suggestions if s.status == "needs_confirm"),
        },
    )
