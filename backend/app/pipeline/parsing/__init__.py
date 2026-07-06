"""Parse stage — turn an uploaded CSV/Excel file into an all-string pandas DataFrame.

Design choices (plan §3.1, §4):
  * XLSX/XLS/ODS are read with **python-calamine** (Rust, no XML-entity parser exposed -> sidesteps
    the XXE class, ~10-50x faster than openpyxl).
  * CSV/TSV encoding is detected with **charset-normalizer**, and the delimiter is auto-sniffed.
  * Everything is read as **string** (``dtype=str``); type coercion is deferred to validation so the
    raw values survive for the error-explanation center. Blank cells become nulls (pd.NA).
  * Hard size caps (rows) defend against huge-file DoS; XLSX zip-bomb/entry caps belong upstream in
    the upload-security layer before bytes reach here.

Returns a ``StageResult[pd.DataFrame]``; ``ok=False`` with an ErrorEnvelope on failure.
"""

from __future__ import annotations

import io
from pathlib import PurePath

import pandas as pd
from charset_normalizer import from_bytes

from app.pipeline.contracts import ErrorCode, ErrorEnvelope, StageName, StageResult

__all__ = ["parse_file", "DEFAULT_MAX_ROWS"]

DEFAULT_MAX_ROWS = 200_000

_CSV_EXTS = {".csv", ".tsv", ".txt"}
_EXCEL_EXTS = {".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"}
# Magic-byte signatures (a defence-in-depth sniff; the upload layer also runs `filetype`).
_ZIP_MAGIC = b"PK\x03\x04"  # xlsx/xlsm/ods are zip archives
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy .xls (OLE2)


def _fail(code: str, message: str, detail: str | None = None) -> StageResult[pd.DataFrame]:
    return StageResult(
        ok=False,
        stage=StageName.PARSE,
        data=None,
        errors=[ErrorEnvelope(code=code, severity="error", stage=StageName.PARSE, message=message, raw=detail)],
        stats={},
    )


def _looks_like_excel(content: bytes, suffix: str) -> bool:
    if suffix in _EXCEL_EXTS:
        return True
    return content[:4] == _ZIP_MAGIC or content[:4] == _OLE_MAGIC


def _read_excel(content: bytes, sheet: str | int | None) -> pd.DataFrame:
    return pd.read_excel(
        io.BytesIO(content),
        sheet_name=0 if sheet is None else sheet,
        engine="calamine",
        dtype=str,
    )


# Restricted to real field separators — NEVER whitespace. pandas' sep=None / csv.Sniffer will
# happily pick a space, which silently shatters single-column values like "HDFC Bank".
_CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]


def _detect_delimiter(text: str) -> str:
    header = next((line for line in text.splitlines() if line.strip()), "")
    counts = {d: header.count(d) for d in _CANDIDATE_DELIMITERS}
    best = max(_CANDIDATE_DELIMITERS, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","  # no separator => single column


def _read_csv(content: bytes) -> pd.DataFrame:
    best = from_bytes(content).best()
    encoding = best.encoding if best is not None else "utf-8"
    text = content.decode(encoding, errors="replace")
    return pd.read_csv(
        io.StringIO(text),
        sep=_detect_delimiter(text),
        engine="python",  # lenient tokenizer (handles ragged rows)
        dtype=str,
        skip_blank_lines=True,
    )


def _clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    before = list(df.columns)
    # drop columns/rows that are entirely empty (common trailing artefacts of exported sheets)
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    surviving = set(df.columns)
    # report dropped columns that had a real header (so the drop is never silent) — ignore "Unnamed: N"
    dropped = [c for c in before if c not in surviving and c and not c.startswith("Unnamed:")]
    return df.reset_index(drop=True), dropped


def parse_file(
    content: bytes,
    filename: str,
    *,
    sheet: str | int | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> StageResult[pd.DataFrame]:
    """Parse ``content`` (raw upload bytes) into an all-string DataFrame."""
    if not content:
        return _fail(ErrorCode.EMPTY_FILE, "The uploaded file is empty.")

    suffix = PurePath(filename).suffix.lower()
    is_excel = _looks_like_excel(content, suffix)

    try:
        df = _read_excel(content, sheet) if is_excel else _read_csv(content)
    except Exception as exc:  # noqa: BLE001 - surface any reader failure as a clean envelope
        return _fail(
            ErrorCode.PARSE_FAILED,
            "Could not read the file. Please check it is a valid CSV or Excel file.",
            detail=f"{type(exc).__name__}: {exc}",
        )

    df, dropped_columns = _clean(df)

    if df.shape[0] == 0 or df.shape[1] == 0:
        return _fail(ErrorCode.EMPTY_FILE, "The file has no data rows.")
    if df.shape[0] > max_rows:
        return _fail(
            ErrorCode.FILE_TOO_LARGE,
            f"The file has {df.shape[0]:,} rows, which exceeds the {max_rows:,}-row limit.",
        )

    return StageResult(
        ok=True,
        stage=StageName.PARSE,
        data=df,
        errors=[],
        stats={"rows": int(df.shape[0]), "columns": int(df.shape[1]), "format": "excel" if is_excel else "csv",
               "dropped_columns": dropped_columns},
    )
