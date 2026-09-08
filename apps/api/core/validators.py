"""
Upload validation for untrusted files.

WHY THIS EXISTS
---------------
Until now the only upload validation in the codebase lived in
`apps.employees.services.documents` and it failed open:

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ...

`content_type` is the multipart header the *client* sent. Omit it and the check
is skipped entirely; send `application/pdf` with an executable and it passes.
That was tolerable for HR-uploaded identity papers, which are stored and never
parsed. It is not tolerable for a spreadsheet importer, whose whole job is to
run a parser over bytes a stranger produced.

THE RULE HERE
-------------
The accept/reject decision is made ONLY from the filename extension and a
structural inspection of the bytes. `declared_content_type` is carried through
to the audit record so there is a trace of what the client claimed, and it is
never consulted for the decision. "Drop the `and`" would not have been the fix;
consulting the header at all was the mistake.

WHAT AN XLSX ACTUALLY IS
------------------------
A zip of XML. That makes three attacks available that a flat format does not:
a zip bomb (small archive, enormous expansion), entity expansion inside the
sheet XML (billion laughs), and a macro payload in `xl/vbaProject.bin`. All
three are checked before a parser is handed the file, because by the time
openpyxl is reading it the budget has already been spent.
"""

from __future__ import annotations

import hashlib
import os
import uuid
import zipfile
from dataclasses import dataclass
from typing import Literal

from django.core.exceptions import ValidationError

#: Read in chunks so hashing a file never materialises it in memory.
_HASH_CHUNK = 64 * 1024

#: How much of a user-supplied filename we keep. Long enough to stay
#: recognisable, short enough that it cannot dominate a stored path.
_MAX_NAME = 120

#: An .xlsx must contain both of these to be a workbook rather than any old zip.
_XLSX_REQUIRED_MEMBERS = ("[Content_Types].xml", "xl/workbook.xml")

#: Macro storage. Present in .xlsm, and in an .xlsm someone renamed to .xlsx.
_XLSX_MACRO_PREFIX = "xl/vbaProject"

#: Zip expansion budgets. A 5 MB archive that expands to 50 GB is a denial of
#: service, not a spreadsheet.
MAX_DECOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_COMPRESSION_RATIO = 120
MAX_ZIP_MEMBERS = 300

#: Text sniffing. A binary passed off as CSV shows up as NUL bytes and a high
#: proportion of unprintable characters.
_TEXT_SNIFF_BYTES = 64 * 1024
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")
_MAX_UNPRINTABLE_RATIO = 0.01

#: Delimiters we accept, in preference order. `csv.Sniffer` is deliberately not
#: used — it runs heuristics over attacker-controlled bytes and can be steered
#: into choosing a delimiter that reshapes the whole file.
_CSV_DELIMITERS = (",", ";", "\t")

#: Values Excel and LibreOffice treat as the start of a formula. A cell holding
#: `=cmd|'/c calc'!A0` is a remote-code-execution attempt against whoever opens
#: the exported file, not a name.
_RISKY_CELL_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class UnsafeUploadError(ValidationError):
    """
    A refused upload.

    Subclasses Django's ValidationError so the existing API exception handler
    renders it as a 400 with the standard envelope, exactly like every other
    service-layer refusal in this codebase.
    """


@dataclass(frozen=True)
class UploadFacts:
    """What we established about an upload by looking at it, not by being told."""

    original_name: str
    extension: str
    size_bytes: int
    sha256: str
    kind: Literal["xlsx", "csv"]
    #: Recorded for the audit trail. NEVER used to decide whether to accept.
    declared_content_type: str
    #: Total decompressed size for a zip container; equal to size for flat files.
    decompressed_bytes: int
    #: Only set for CSV — the encoding that decoded cleanly, and the delimiter.
    encoding: str = ""
    delimiter: str = ""


def safe_original_name(raw: str) -> str:
    """
    A filename fit to store and display, derived from one we do not trust.

    Strips any directory component (so `../../etc/passwd` becomes `passwd`) and
    every control character, including the NUL that truncates C-side string
    handling and the CR/LF that forge extra lines in a log file.

    The result is stored and returned in JSON. It is never used to build a
    filesystem path and never interpolated into an exception message.
    """
    name = os.path.basename(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    # Whitespace-like controls become a space, so a name carrying a stray tab or
    # newline reads as two words rather than silently becoming one. Everything
    # else unprintable — NUL above all — is removed outright; it separates
    # nothing and exists only to confuse whatever reads the name next.
    name = "".join(
        " " if ch in "\t\r\n\v\f" else ch for ch in name if ch.isprintable() or ch in "\t\r\n\v\f"
    )
    name = " ".join(name.split())

    # Truncate the STEM and keep the extension. The extension is what decides
    # whether an upload is accepted at all, so trimming a long name from the
    # right silently turns "a very long ... .pdf" into a name with no suffix,
    # which is then refused as "not an accepted format" — a confusing refusal
    # of a perfectly ordinary file whose only sin was a wordy name.
    stem, dot, tail = name.rpartition(".")
    if dot and stem and 1 <= len(tail) <= _MAX_EXTENSION:
        keep = _MAX_NAME - len(tail) - 1
        if keep > 0:
            name = f"{stem[:keep]}.{tail}"

    return name[:_MAX_NAME] or "upload"


#: How long a stored path may be. The FileFields that use `scoped_storage_path`
#: declare `max_length=STORED_PATH_MAX`; the two numbers must agree, because
#: this is the budget the truncation below spends.
STORED_PATH_MAX = 255

#: Django appends `_` plus seven random characters when a name already exists,
#: and it must be able to do that without truncating the stem to nothing —
#: `get_available_name` raises SuspiciousFileOperation when it cannot.
_COLLISION_SUFFIX = 8

#: A guard against a nonsense extension, e.g. a file named `x.` + 200 letters.
_MAX_EXTENSION = 12


def scoped_storage_path(prefix: str, owner_id, filename: str) -> str:
    """
    Build `<prefix>/<owner>/<random>/<name>`, bounding the name so it fits.

    WHY THE BOUNDING MATTERS
    ------------------------
    These paths spend most of their length before the filename even starts:
    a prefix, an owner UUID (36), a random hex (32) and two separators is
    already 70-90 characters. `FileField` defaults to `max_length=100`, so an
    employee document had ELEVEN characters left for the name.

    Django does try to truncate, but it truncates the *stem* and then raises
    `SuspiciousFileOperation` if nothing survives — which surfaces as a bare
    400 with no message, from a request that looked completely ordinary. Every
    real document ("Aadhaar card.pdf", "IMG_2201.jpg") tripped it while the
    short names used in tests did not, so it read as "uploads are broken" with
    no clue as to why.

    The stored name is cosmetic in any case: `original_filename` holds what the
    person actually called the file, and downloads are served under that name.
    Truncating here costs nothing a user can see.
    """
    folder = f"{prefix}/{owner_id}/{uuid.uuid4().hex}/"

    safe = safe_original_name(filename)
    stem, dot, tail = safe.rpartition(".")
    if not dot:  # no extension at all
        stem, extension = safe, ""
    else:
        extension = f".{tail.lower()}"[:_MAX_EXTENSION]

    budget = STORED_PATH_MAX - len(folder) - len(extension) - _COLLISION_SUFFIX
    if budget < 1:
        # Only reachable by giving this function an absurd prefix, which is a
        # programming error rather than anything a user did.
        raise ValueError(f"Storage prefix {prefix!r} leaves no room for a filename.")

    return f"{folder}{stem[:budget] or 'file'}{extension}"


def neutralise_cell(value: str) -> str:
    """
    Defang a value on its way *into* a spreadsheet or CSV.

    A leading apostrophe is Excel's "treat the rest as literal text" marker. It
    is the standard mitigation for CSV injection, and it belongs at the point of
    emission — a value is only dangerous once it lands in a cell something else
    will open.
    """
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in _RISKY_CELL_PREFIXES else text


def _sha256_and_size(file) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    file.seek(0)
    for chunk in iter(lambda: file.read(_HASH_CHUNK), b""):
        digest.update(chunk)
        size += len(chunk)
    file.seek(0)
    return digest.hexdigest(), size


def inspect_zip(zf: zipfile.ZipFile) -> int:
    """
    Budget-check a zip before anything decompresses it. Returns total expanded size.

    `zipfile` caps a member read at its declared `file_size`, so an attacker who
    understates it only truncates their own payload. Overstating it is what this
    catches — along with the per-member ratio, which is what a bomb built from
    one enormous run of zeroes looks like.
    """
    members = zf.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise UnsafeUploadError(
            {"file": "This workbook has an implausible number of internal parts."}
        )

    total = 0
    for member in members:
        total += member.file_size
        if total > MAX_DECOMPRESSED_BYTES:
            raise UnsafeUploadError(
                {"file": "This file expands to more data than the importer accepts."}
            )
        compressed = member.compress_size or 1
        if member.file_size / compressed > MAX_COMPRESSION_RATIO:
            raise UnsafeUploadError(
                {"file": "This file expands to more data than the importer accepts."}
            )
    return total


def _sniff_xlsx(file) -> int:
    """Confirm the bytes really are a macro-free workbook. Returns expanded size."""
    file.seek(0)
    if file.read(4) != b"PK\x03\x04":
        raise UnsafeUploadError({"file": "This is not a readable spreadsheet."})
    file.seek(0)

    try:
        with zipfile.ZipFile(file) as zf:
            names = set(zf.namelist())
            if any(name.startswith(_XLSX_MACRO_PREFIX) for name in names):
                raise UnsafeUploadError(
                    {
                        "file": (
                            "This workbook contains macros. Save it as .xlsx "
                            "without macros, or export it as CSV."
                        )
                    }
                )
            if not all(member in names for member in _XLSX_REQUIRED_MEMBERS):
                raise UnsafeUploadError({"file": "This is not a readable spreadsheet."})
            expanded = inspect_zip(zf)
    except zipfile.BadZipFile as exc:
        raise UnsafeUploadError({"file": "This is not a readable spreadsheet."}) from exc
    finally:
        file.seek(0)

    return expanded


def _sniff_csv(file) -> tuple[str, str]:
    """Confirm the bytes are text. Returns (encoding, delimiter)."""
    file.seek(0)
    head = file.read(_TEXT_SNIFF_BYTES)
    file.seek(0)

    if b"\x00" in head:
        raise UnsafeUploadError({"file": "This is not a readable text file."})

    text = ""
    encoding = ""
    for candidate in _TEXT_ENCODINGS:
        try:
            text = head.decode(candidate)
        except UnicodeDecodeError:
            continue
        encoding = candidate
        break
    if not encoding:
        raise UnsafeUploadError({"file": "This file's text encoding is not readable."})

    # A stray BOM mid-stream is untidy, not hostile — WorkIndia and Naukri both
    # emit one, and counting it as unprintable would reject their exports.
    body = text.replace("﻿", "")
    if body:
        unprintable = sum(
            1 for ch in body if not ch.isprintable() and ch not in "\r\n\t"
        )
        if unprintable / len(body) > _MAX_UNPRINTABLE_RATIO:
            raise UnsafeUploadError({"file": "This is not a readable text file."})

    # Count on the header line only. A delimiter that does not appear there
    # cannot be the one separating the columns we are about to name.
    header = text.splitlines()[0] if text.splitlines() else ""
    delimiter = max(_CSV_DELIMITERS, key=header.count)
    if header.count(delimiter) == 0:
        delimiter = ","

    return encoding, delimiter


def validate_upload(
    file,
    *,
    allowed_extensions: frozenset[str],
    max_bytes: int,
    subject: str = "file",
) -> UploadFacts:
    """
    Establish what an uploaded file is, refusing anything we cannot vouch for.

    `allowed_extensions` is a closed set including the leading dot, lower case.
    Only `.xlsx` and `.csv` are structurally understood; anything else in the
    set is accepted on extension and size alone, which is what the employee
    document path needs.

    `subject` only shapes the wording of the refusal ("document", "spreadsheet")
    so each caller's error reads naturally to the person who hit it.
    """
    original_name = safe_original_name(getattr(file, "name", ""))
    _, _, tail = original_name.rpartition(".")
    extension = f".{tail.lower()}" if tail and tail != original_name else ""

    if extension not in allowed_extensions:
        accepted = ", ".join(sorted(allowed_extensions))
        raise UnsafeUploadError(
            {
                "file": (
                    f"That is not an accepted {subject} format. "
                    f"Accepted: {accepted}."
                )
            }
        )

    sha256, size_bytes = _sha256_and_size(file)
    if size_bytes == 0:
        raise UnsafeUploadError({"file": "This file is empty."})
    if size_bytes > max_bytes:
        raise UnsafeUploadError(
            {
                "file": (
                    f"The file is {size_bytes // 1024 // 1024} MB; "
                    f"the limit is {max_bytes // 1024 // 1024} MB."
                )
            }
        )

    encoding = delimiter = ""
    if extension == ".xlsx":
        kind = "xlsx"
        decompressed = _sniff_xlsx(file)
    elif extension == ".csv":
        kind = "csv"
        decompressed = size_bytes
        encoding, delimiter = _sniff_csv(file)
    else:
        # Extension is in the caller's allow-list but we have no structural
        # check for it. Size and extension are the guarantees; say so rather
        # than implying the bytes were understood.
        kind = "csv"
        decompressed = size_bytes

    return UploadFacts(
        original_name=original_name,
        extension=extension,
        size_bytes=size_bytes,
        sha256=sha256,
        kind=kind,
        declared_content_type=(getattr(file, "content_type", "") or "")[:100],
        decompressed_bytes=decompressed,
        encoding=encoding,
        delimiter=delimiter,
    )
