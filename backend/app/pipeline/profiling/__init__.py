"""Profile stage — derive compact per-column signals that drive the mapping UI and validation.

Hot-path signals only (type, null %, cardinality, sample values, candidate-key flag). These come
from pandas built-ins so the stage is dependency-light and fast in the request path; the heavy
HTML EDA report (fg-data-profiling, internal/ops only) is a separate, out-of-band concern (plan §3.1).

The output ``ProfileSignals`` is what the auto-mapper's type-compatibility gate and the validation
suite consume, and what the React preview screen renders.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pandas as pd
from pydantic import BaseModel, Field

from app.pipeline.contracts import StageName, StageResult

__all__ = ["profile", "ColumnProfile", "ProfileSignals"]

_BOOLISH = {"yes", "no", "true", "false", "1", "0", "y", "n"}
_CANDIDATE_KEY_DISTINCT = 0.99
_CANDIDATE_KEY_NULL = 0.01
_TYPE_THRESHOLD = 0.8


class ColumnProfile(BaseModel):
    name: str
    inferred_type: str  # numeric | boolean | string | empty
    count: int          # non-null, non-blank values
    null_pct: float
    distinct: int
    distinct_pct: float
    samples: list[str] = Field(default_factory=list)
    is_candidate_key: bool = False


class ProfileSignals(BaseModel):
    row_count: int
    column_count: int
    columns: list[ColumnProfile] = Field(default_factory=list)

    def by_name(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


def _norm(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def _is_number(value: object) -> bool:
    try:
        Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return False
    return True


def _non_blank(series: pd.Series) -> list[object]:
    return [
        v
        for v in series.tolist()
        if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""
    ]


def _profile_column(name: str, series: pd.Series, total: int) -> ColumnProfile:
    values = _non_blank(series)
    n = len(values)
    if n == 0:
        return ColumnProfile(
            name=name, inferred_type="empty", count=0, null_pct=1.0, distinct=0, distinct_pct=0.0
        )

    distinct_values = {_norm(v) for v in values}
    distinct = len(distinct_values)
    numeric_ratio = sum(_is_number(v) for v in values) / n
    boolish_ratio = sum(_norm(v) in _BOOLISH for v in values) / n

    if boolish_ratio >= _TYPE_THRESHOLD:
        inferred = "boolean"
    elif numeric_ratio >= _TYPE_THRESHOLD:
        inferred = "numeric"
    else:
        inferred = "string"

    null_pct = 1.0 - (n / total) if total else 0.0
    distinct_pct = distinct / n

    # first few distinct sample values, in original (non-normalized) form
    samples: list[str] = []
    seen: set[str] = set()
    for v in values:
        key = _norm(v)
        if key not in seen:
            seen.add(key)
            samples.append(str(v))
        if len(samples) >= 3:
            break

    return ColumnProfile(
        name=name,
        inferred_type=inferred,
        count=n,
        null_pct=round(null_pct, 4),
        distinct=distinct,
        distinct_pct=round(distinct_pct, 4),
        samples=samples,
        is_candidate_key=distinct_pct >= _CANDIDATE_KEY_DISTINCT and null_pct <= _CANDIDATE_KEY_NULL,
    )


def profile(df: pd.DataFrame) -> StageResult[ProfileSignals]:
    """Compute compact profiling signals for every column of ``df``."""
    total = int(df.shape[0])
    columns = [_profile_column(str(col), df[col], total) for col in df.columns]
    signals = ProfileSignals(row_count=total, column_count=int(df.shape[1]), columns=columns)
    return StageResult(
        ok=True,
        stage=StageName.PROFILE,
        data=signals,
        errors=[],
        stats={"rows": total, "columns": int(df.shape[1])},
    )
