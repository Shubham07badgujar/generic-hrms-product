"""
Turning an untrusted spreadsheet into rows.

Everything here assumes the file was written by someone hostile. `core.validators`
has already established that it is structurally a workbook or text and is within
its expansion budget; this module adds the limits that only matter once you are
actually walking the contents.

THE LIMITS ARE NOT ADVISORY
---------------------------
`ws.max_row` comes from a `<dimension>` record the file's author wrote. A sheet
can claim 1,048,576 rows and contain three. Nothing here allocates from that
number — rows are counted while streaming and the walk stops at MAX_ROWS.

`data_only=True` IS A SECURITY CONTROL
--------------------------------------
Without it a formula cell reads back as its literal text, so
`=cmd|'/c calc'!A0` lands in `first_name` and detonates in whatever opens the
next export. With it, openpyxl returns the value Excel cached and never
evaluates anything.

Its trap is the other half: for a formula cell with NO cached value — which is
what any file written by openpyxl or a stripped LibreOffice save produces —
`data_only=True` returns None. A required column full of formulas would import
as silently blank, so that case is reported rather than accepted.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError

from apps.imports.platforms.registry import (
    CANONICAL_FIELDS,
    PlatformSpec,
    normalise_header,
)

MAX_ROWS = 2_000
#: Naukri's Response Management export genuinely ships 79 columns, so the
#: ceiling sits above the widest real export while still bounding a hostile one.
MAX_COLUMNS = 96
MAX_CELL_CHARS = 512
MAX_SHEETS = 20
PARSE_BUDGET_SECONDS = 20
#: Guards against a single CSV field holding megabytes of text.
CSV_FIELD_LIMIT = 32_768


class ParseError(ValidationError):
    """The file could not be read as a candidate export."""


@dataclass
class ParsedFile:
    headers: list[str]
    #: canonical field -> the header it came from. Column names only.
    mapping: dict[str, str] = field(default_factory=dict)
    #: One dict per row, keyed by canonical field, plus "_raw" and "_row".
    rows: list[dict] = field(default_factory=list)
    unmapped_headers: list[str] = field(default_factory=list)
    truncated: bool = False


def _clip(value) -> str:
    """
    Stringify and bound a cell.

    A 10 MB cell is not a name. Truncating rather than refusing keeps one absurd
    cell from failing an otherwise good file, and the value is bounded before it
    reaches any column, log or JSON blob.
    """
    if value is None:
        return ""
    text = str(value).strip()
    return text[:MAX_CELL_CHARS]


def _resolve_mapping(headers: list[str], spec: PlatformSpec, override: dict | None):
    """
    header index -> canonical field, from the spec's aliases or an override.

    An override target MUST be one of CANONICAL_FIELDS. Downstream,
    `_normalise_row` reads a fixed set of keys and would ignore anything else,
    so an arbitrary target is not presently assignable — but that is defence by
    accident. Refusing it here makes "a mapping cannot name an arbitrary model
    field" a property of the code rather than a happy consequence of another
    function's shape.
    """
    aliases = spec.alias_map()
    raw_override = override or {}

    unknown = sorted(set(raw_override.values()) - set(CANONICAL_FIELDS))
    if unknown:
        raise ParseError(
            {
                "column_override": (
                    f"Cannot map a column to {', '.join(unknown)}. "
                    f"Choose one of: {', '.join(CANONICAL_FIELDS)}."
                )
            }
        )

    override = {normalise_header(k): v for k, v in raw_override.items()}

    mapping: dict[int, str] = {}
    used: dict[str, str] = {}
    unmapped: list[str] = []

    for index, header in enumerate(headers):
        key = normalise_header(header)
        canonical = override.get(key) or aliases.get(key)
        if canonical and canonical not in used:
            mapping[index] = canonical
            used[canonical] = header
        elif not canonical:
            unmapped.append(header)

    return mapping, used, unmapped


def _assemble(mapping, headers, values, row_number, spec):
    """One spreadsheet row -> canonical dict, with the raw cells alongside."""
    raw = {}
    row: dict = {"_row": row_number}

    for index, value in enumerate(values):
        if index >= MAX_COLUMNS:
            break
        cell = _clip(value)
        header = headers[index] if index < len(headers) else f"column_{index}"
        if cell:
            raw[header] = cell
        canonical = mapping.get(index)
        if not canonical:
            continue
        parser = spec.parser_for(canonical)
        row[canonical] = parser(cell) if parser else cell

    row["_raw"] = raw

    # A name split across two columns, or supplied whole — accept either.
    if not row.get("first_name") and row.get("full_name"):
        parts = str(row["full_name"]).split()
        row["first_name"] = parts[0] if parts else ""
        if len(parts) > 1 and not row.get("last_name"):
            row["last_name"] = " ".join(parts[1:])

    return row


def _iter_xlsx(file, spec, override):
    import openpyxl

    workbook = openpyxl.load_workbook(
        file, read_only=True, data_only=True, keep_links=False
    )
    try:
        if len(workbook.sheetnames) > MAX_SHEETS:
            raise ParseError({"file": "This workbook has too many sheets to read."})
        # First sheet only. Walking every sheet multiplies the attack surface
        # and no platform export puts candidates on a second tab.
        sheet = workbook[workbook.sheetnames[0]]

        started = time.monotonic()
        headers: list[str] = []
        mapping: dict[int, str] = {}
        used: dict[str, str] = {}
        unmapped: list[str] = []
        rows: list[dict] = []
        truncated = False

        for index, values in enumerate(sheet.iter_rows(values_only=True)):
            if time.monotonic() - started > PARSE_BUDGET_SECONDS:
                raise ParseError({"file": "This file took too long to read."})

            if index == 0:
                headers = [_clip(v) for v in values][:MAX_COLUMNS]
                mapping, used, unmapped = _resolve_mapping(headers, spec, override)
                continue

            if len(rows) >= MAX_ROWS:
                truncated = True
                break

            if not any(v is not None and str(v).strip() for v in values):
                continue  # blank spacer row

            rows.append(_assemble(mapping, headers, values, index + 1, spec))

        return ParsedFile(headers, used, rows, unmapped, truncated)
    finally:
        # read_only mode holds the zip open until this is called.
        workbook.close()


def _iter_csv(file, spec, override, *, encoding: str, delimiter: str):
    csv.field_size_limit(CSV_FIELD_LIMIT)

    file.seek(0)
    text = io.TextIOWrapper(file, encoding=encoding, newline="")
    reader = csv.reader(text, delimiter=delimiter)

    started = time.monotonic()
    headers: list[str] = []
    mapping: dict[int, str] = {}
    used: dict[str, str] = {}
    unmapped: list[str] = []
    rows: list[dict] = []
    truncated = False

    for index, values in enumerate(reader):
        if time.monotonic() - started > PARSE_BUDGET_SECONDS:
            raise ParseError({"file": "This file took too long to read."})

        if index == 0:
            headers = [_clip(v) for v in values][:MAX_COLUMNS]
            mapping, used, unmapped = _resolve_mapping(headers, spec, override)
            continue

        if len(rows) >= MAX_ROWS:
            truncated = True
            break

        if not any(str(v).strip() for v in values):
            continue

        rows.append(_assemble(mapping, headers, values, index + 1, spec))

    return ParsedFile(headers, used, rows, unmapped, truncated)


def parse(file, *, facts, spec: PlatformSpec, column_override: dict | None = None):
    """
    Read a validated upload into canonical rows.

    `facts` is the `UploadFacts` from `core.validators.validate_upload`, which
    has already decided the file is what it claims to be. This function assumes
    that check happened and does not repeat it.
    """
    if not spec.available:
        raise ParseError({"platform": spec.unavailable_reason})

    if facts.kind == "xlsx":
        parsed = _iter_xlsx(file, spec, column_override)
    else:
        parsed = _iter_csv(
            file, spec, column_override,
            encoding=facts.encoding or "utf-8",
            delimiter=facts.delimiter or ",",
        )

    if not parsed.mapping:
        # The headers travel with the refusal. Without them the caller can only
        # be told "unrecognised", which dead-ends a perfectly good export whose
        # column names simply differ from the aliases we know.
        raise ParseError(
            {
                "file": (
                    "None of this file's columns could be recognised. Map them "
                    "manually, or check it is the export you meant."
                ),
                "detected_headers": parsed.headers,
            }
        )
    return parsed
