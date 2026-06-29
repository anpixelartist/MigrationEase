"""Tests for the hardened Tally response parser (anti-XXE, fail-closed)."""

import pytest

from app.pipeline.push.response_parser import XMLSecurityError, parse_import_response

SUCCESS_WITH_ERROR = (
    b"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
    b"<BODY><DATA><IMPORTRESULT>"
    b"<CREATED>12</CREATED><ALTERED>3</ALTERED><DELETED>0</DELETED>"
    b"<COMBINED>0</COMBINED><IGNORED>1</IGNORED><ERRORS>1</ERRORS><EXCEPTIONS>0</EXCEPTIONS>"
    b"<LASTVCHID>0</LASTVCHID><LASTMID>41233</LASTMID>"
    b"<LINEERROR>Parent group 'Sundry Debtorss' does not exist</LINEERROR>"
    b"</IMPORTRESULT></DATA></BODY></ENVELOPE>"
)


def test_parse_counts_and_line_error() -> None:
    result = parse_import_response(SUCCESS_WITH_ERROR)
    assert result.created == 12
    assert result.altered == 3
    assert result.ignored == 1
    assert result.errors == 1
    assert result.last_master_id == 41233
    assert result.line_errors == ["Parent group 'Sundry Debtorss' does not exist"]
    assert result.is_success is False


def test_parse_multiple_line_errors() -> None:
    xml = (
        b"<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>0</CREATED><ERRORS>2</ERRORS>"
        b"<LINEERROR>err one</LINEERROR><LINEERROR>err two</LINEERROR>"
        b"</IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )
    assert parse_import_response(xml).line_errors == ["err one", "err two"]


def test_success_response() -> None:
    xml = (
        b"<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>5</CREATED><ALTERED>0</ALTERED>"
        b"<ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )
    result = parse_import_response(xml)
    assert result.created == 5
    assert result.is_success is True


def test_case_insensitive_tags() -> None:
    xml = (
        b"<envelope><body><data><importresult><created>7</created>"
        b"</importresult></data></body></envelope>"
    )
    assert parse_import_response(xml).created == 7


def test_doctype_xxe_rejected() -> None:
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<ENVELOPE><A>&x;</A></ENVELOPE>"
    )
    with pytest.raises(XMLSecurityError):
        parse_import_response(xxe)


def test_malformed_rejected() -> None:
    with pytest.raises(XMLSecurityError):
        parse_import_response(b"<ENVELOPE><UNCLOSED>")
