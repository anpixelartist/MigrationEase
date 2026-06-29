"""Serialization helpers for Tally import XML.

Two Tally-specific quirks are handled here:

1. **List marker.** Tally prefixes certain enum-ish values (e.g. ``GSTAPPLICABLE``, ``STATENAME``)
   with the control character ASCII 4, written in the wire format as the character reference
   ``&#4;``. That code point is *invalid* in XML 1.0, so lxml refuses to put it in element text.
   We therefore place an ASCII sentinel token in the tree (which keeps the tree well-formed and
   lets lxml guarantee structure/escaping) and substitute the literal ``&#4;`` bytes only at the
   final serialization step. The final bytes are never strict-parsed by us.

2. **No BOM, explicit encoding.** TallyPrime's HTTP import has historically choked on a UTF-8 BOM.
   We serialize as ASCII with numeric character references (so non-ASCII names survive) and no XML
   declaration / BOM by default. The exact encoding/declaration Tally accepts is confirmed in
   Milestone 0 Spike A (docs/ plan §12); keep these as the single knob.
"""

from __future__ import annotations

from lxml import etree

# Sentinel that cannot collide with real data; replaced post-serialization.
_LIST_MARKER_TOKEN = "@@TALLY_LIST_MARKER@@"
_LIST_MARKER_OUTPUT = b"&#4;"

# Defaults verified in Milestone 0 Spike A — keep them here as the single source of truth.
DEFAULT_ENCODING = "ascii"
DEFAULT_XML_DECLARATION = False


def list_marker_value(label: str) -> str:
    """Return a Tally list value such as ``&#4; Applicable`` (token form, substituted on serialize)."""
    return f"{_LIST_MARKER_TOKEN} {label}"


def serialize(
    element: etree._Element,
    *,
    encoding: str = DEFAULT_ENCODING,
    xml_declaration: bool = DEFAULT_XML_DECLARATION,
) -> bytes:
    """Serialize an lxml element to Tally-ready bytes (no BOM; list markers substituted)."""
    raw = etree.tostring(
        element,
        encoding=encoding,
        xml_declaration=xml_declaration,
        pretty_print=False,
    )
    return raw.replace(_LIST_MARKER_TOKEN.encode("ascii"), _LIST_MARKER_OUTPUT)
