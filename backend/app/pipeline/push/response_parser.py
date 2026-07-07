"""Parse Tally's import response XML safely.

Tally's response is untrusted output relayed from a desktop app, so we:
  * reject any DOCTYPE (defence-in-depth against XXE / entity-expansion);
  * parse with a hardened lxml parser (no entity resolution, no network, no DTD, tree-size guard on);
  * read counts case-insensitively and collect *all* <LINEERROR> elements (there can be many).

Note: ``defusedxml.lxml`` is deprecated, so we configure lxml directly (plan §3.1). Pin lxml>=6.1.1.
The bridge must also bound the response byte size before this is called.
"""

from __future__ import annotations

from lxml import etree
from pydantic import BaseModel, Field, computed_field

# Tally import-result counters (uppercased local names). The real TallyPrime response root is
# <RESPONSE> and includes <CANCELLED> (verified Milestone 0 Spike A, 2026-06-26).
_COUNT_TAGS = frozenset(
    {
        "CREATED",
        "ALTERED",
        "DELETED",
        "COMBINED",
        "IGNORED",
        "ERRORS",
        "CANCELLED",
        "EXCEPTIONS",
        "LASTVCHID",
        "LASTMID",
    }
)


class XMLSecurityError(Exception):
    """Raised when a response is malformed or violates the safe-parse policy (e.g. contains a DOCTYPE)."""


class ImportResult(BaseModel):
    created: int = 0
    altered: int = 0
    deleted: int = 0
    combined: int = 0
    ignored: int = 0
    errors: int = 0
    cancelled: int = 0
    exceptions: int = 0
    last_vch_id: int = 0
    last_master_id: int = 0
    line_errors: list[str] = Field(default_factory=list)
    raw_xml: str = ""

    @computed_field  # serialized into the push result so the UI can flag partial/failed imports
    @property
    def is_success(self) -> bool:
        return (
            self.errors == 0
            and self.exceptions == 0
            and self.cancelled == 0
            and not self.line_errors
        )


def _hardened_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,  # keep the default tree-size / expansion guards ON
        recover=False,
    )


def _to_int(text: str | None) -> int:
    try:
        return int((text or "0").strip())
    except (TypeError, ValueError):
        return 0


def parse_import_response(data: bytes | str) -> ImportResult:
    """Parse a Tally import response into a structured :class:`ImportResult`.

    Raises :class:`XMLSecurityError` on a DOCTYPE or malformed XML.
    """
    raw_bytes = data.encode("utf-8", "replace") if isinstance(data, str) else bytes(data)

    if b"<!DOCTYPE" in raw_bytes.upper():
        raise XMLSecurityError("DOCTYPE declarations are not allowed in Tally responses")

    try:
        root = etree.fromstring(raw_bytes, parser=_hardened_parser())
    except etree.XMLSyntaxError as exc:  # fail closed
        raise XMLSecurityError(f"Malformed Tally response: {exc}") from exc

    counts: dict[str, int] = {tag: 0 for tag in _COUNT_TAGS}
    line_errors: list[str] = []

    for element in root.iter():
        try:
            local = etree.QName(element).localname.upper()
        except ValueError:
            continue  # comments / processing instructions
        if local in _COUNT_TAGS:
            counts[local] = _to_int(element.text)
        elif local == "LINEERROR" and element.text and element.text.strip():
            line_errors.append(element.text.strip())

    return ImportResult(
        created=counts["CREATED"],
        altered=counts["ALTERED"],
        deleted=counts["DELETED"],
        combined=counts["COMBINED"],
        ignored=counts["IGNORED"],
        errors=counts["ERRORS"],
        cancelled=counts["CANCELLED"],
        exceptions=counts["EXCEPTIONS"],
        last_vch_id=counts["LASTVCHID"],
        last_master_id=counts["LASTMID"],
        line_errors=line_errors,
        raw_xml=raw_bytes.decode("utf-8", "replace"),
    )
