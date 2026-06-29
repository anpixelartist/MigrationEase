"""Entity resolution — classify each incoming master vs the existing Tally master snapshot.

Deterministic and explainable by design (plan §6): a wrong Create-vs-Alter writes bad financial
data, so we use a transparent normalize -> block -> score -> threshold rule, not a black-box model.

Scoring (per best candidate): ``0.60*name_exact + 0.30*name_jw + 0.10*parent_exact``.
Decision:
  * UPDATE (ACTION=Alter)  — exact normalized name, or score >= 0.95, and not ambiguous
  * CONFLICT (held)        — ambiguous top-2, or score >= 0.85, or a high non-exact name similarity
  * CREATE (ACTION=Create) — otherwise

Identity is **company-global NAME** for ledgers (parent is an attribute, not a key) and NAME within
class for groups/stock items/units (plan §11.6); parent only nudges the score.

Existing masters are dicts with keys: ``name`` (required), ``parent``, ``guid``, ``alter_id``.
``CONFLICT`` rows are held out of the XML batch until a human resolves them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

import jellyfish
from pydantic import BaseModel, Field
from rapidfuzz.distance import JaroWinkler

MATCH_THRESHOLD = 0.95
CONFLICT_THRESHOLD = 0.85
NAME_SIMILARITY_CONFLICT = 0.90  # a close non-exact name -> possible match (held for review)
AMBIGUOUS_GAP = 0.05

W_EXACT, W_JW, W_PARENT = 0.60, 0.30, 0.10

Decision = Literal["create", "update", "conflict"]


class Candidate(BaseModel):
    name: str
    parent: str | None = None
    guid: str | None = None
    score: float


class ResolutionVerdict(BaseModel):
    source_row: int | None = None
    incoming_name: str
    decision: Decision
    action: Literal["Create", "Alter"] | None  # None for held conflicts
    matched_name: str | None = None
    matched_guid: str | None = None
    score: float = 0.0
    candidates: list[Candidate] = Field(default_factory=list)


def _norm(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().casefold().split())


def _block_keys(name_norm: str) -> set[str]:
    keys: set[str] = set()
    if name_norm:
        keys.add("p:" + name_norm[:4])
        try:
            metaphone = jellyfish.metaphone(name_norm)
        except Exception:  # pragma: no cover - jellyfish is robust, but never let blocking crash
            metaphone = ""
        if metaphone:
            keys.add("m:" + metaphone)
    return keys


def resolve(
    incoming: Sequence[Mapping[str, object]],
    existing: Sequence[Mapping[str, object]],
) -> list[ResolutionVerdict]:
    """Return a verdict per incoming row. ``incoming``/``existing`` rows are name/parent/guid maps."""
    existing_norm = [_norm(e.get("name")) for e in existing]
    exact_index: dict[str, int] = {}
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, name_norm in enumerate(existing_norm):
        exact_index.setdefault(name_norm, i)
        for key in _block_keys(name_norm):
            blocks[key].append(i)

    verdicts: list[ResolutionVerdict] = []
    for row in incoming:
        name = str(row.get("name") or "")
        name_norm = _norm(name)
        parent_norm = _norm(row.get("parent"))

        candidate_ids: set[int] = set()
        if name_norm in exact_index:
            candidate_ids.add(exact_index[name_norm])
        for key in _block_keys(name_norm):
            candidate_ids.update(blocks.get(key, []))

        scored: list[tuple[float, float, Mapping[str, object]]] = []
        for i in candidate_ids:
            existing_row = existing[i]
            other_norm = existing_norm[i]
            name_exact = 1.0 if other_norm == name_norm and name_norm else 0.0
            name_jw = JaroWinkler.similarity(name_norm, other_norm) if name_norm and other_norm else 0.0
            parent_exact = 1.0 if parent_norm and parent_norm == _norm(existing_row.get("parent")) else 0.0
            score = W_EXACT * name_exact + W_JW * name_jw + W_PARENT * parent_exact
            scored.append((score, name_jw, existing_row))
        scored.sort(key=lambda t: t[0], reverse=True)
        verdicts.append(_decide(row, name, name_norm, scored))
    return verdicts


def _decide(
    row: Mapping[str, object],
    name: str,
    name_norm: str,
    scored: list[tuple[float, float, Mapping[str, object]]],
) -> ResolutionVerdict:
    source_row = row.get("source_row")
    source_row_int = int(source_row) if isinstance(source_row, int) else None

    if not scored:
        return ResolutionVerdict(
            source_row=source_row_int, incoming_name=name, decision="create", action="Create"
        )

    best_score, best_jw, best = scored[0]
    candidates = [
        Candidate(name=str(e.get("name")), parent=_opt(e.get("parent")), guid=_opt(e.get("guid")), score=round(s, 4))
        for s, _, e in scored[:3]
    ]
    name_exact = name_norm == _norm(best.get("name")) and bool(name_norm)
    ambiguous = (
        len(scored) >= 2
        and scored[1][0] >= CONFLICT_THRESHOLD
        and (scored[0][0] - scored[1][0]) < AMBIGUOUS_GAP
    )

    base = {
        "source_row": source_row_int,
        "incoming_name": name,
        "matched_name": str(best.get("name")),
        "matched_guid": _opt(best.get("guid")),
        "score": round(best_score, 4),
        "candidates": candidates,
    }

    if (name_exact or best_score >= MATCH_THRESHOLD) and not ambiguous:
        return ResolutionVerdict(decision="update", action="Alter", **base)
    if ambiguous or best_score >= CONFLICT_THRESHOLD or best_jw >= NAME_SIMILARITY_CONFLICT:
        return ResolutionVerdict(decision="conflict", action=None, **base)
    return ResolutionVerdict(decision="create", action="Create", **base)


def _opt(value: object) -> str | None:
    return None if value is None else str(value)
