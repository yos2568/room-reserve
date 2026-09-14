"""CSV reading and writing with spreadsheet-safe output.

V3 section 10 requires formula-safe CSV exports. A leading ``=``, ``+``, ``-``,
``@``, tab or carriage return makes Excel and Sheets treat a cell as a formula,
so exported text is prefixed with an apostrophe and quoted.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

# Characters that turn a cell into a formula in common spreadsheet software.
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Neutralise a value for spreadsheet export."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(DANGEROUS_PREFIXES):
        return "'" + text
    return text


def write_csv(headers: Iterable[str], rows: Iterable[Iterable], *, include_bom: bool = True) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([safe_cell(header) for header in headers])
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    text = buffer.getvalue()
    # A BOM keeps Thai text readable when the file is opened directly in Excel.
    return ("\ufeff" + text) if include_bom else text


def read_csv(text: str) -> list[dict[str, str]]:
    """Parse an uploaded roster into dictionaries keyed by header name."""
    if text.startswith("\ufeff"):
        text = text[1:]
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [{(key or "").strip().lower(): (value or "").strip() for key, value in row.items()} for row in reader]
