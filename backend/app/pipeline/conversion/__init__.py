"""Tally XML generation (the safety-critical spine).

Build with lxml element-tree construction (auto-escaping) — never string/Jinja templating.
See ``builder.build_masters_envelope`` and ``serialize.serialize``.
"""

from app.pipeline.conversion.builder import (
    build_and_serialize,
    build_masters_envelope,
    format_opening_balance,
    topological_sort_groups,
)
from app.pipeline.conversion.serialize import serialize

__all__ = [
    "build_masters_envelope",
    "build_and_serialize",
    "serialize",
    "format_opening_balance",
    "topological_sort_groups",
]
