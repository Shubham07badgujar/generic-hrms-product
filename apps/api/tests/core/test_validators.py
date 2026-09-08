"""
Upload validation against files built to be hostile.

Every fixture here is synthesised in-process. Nothing on disk, no real PII, and
no sample downloaded from anywhere — a test suite for malicious uploads should
not itself ship malicious uploads.

The controls under test are the ones that stand between an anonymous
spreadsheet and a parser: extension allow-listing that cannot be talked out of
its decision, structural inspection of the container, expansion budgets, and
macro refusal.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.validators import (
    MAX_COMPRESSION_RATIO,
    UnsafeUploadError,
    neutralise_cell,
    safe_original_name,
    validate_upload,
)

SPREADSHEETS = frozenset({".xlsx", ".csv"})
ONE_MB = 1024 * 1024


def _upload(name: str, payload: bytes, content_type: str = "") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, payload, content_type=content_type)


def _xlsx_bytes(extra: dict[str, bytes] | None = None, *, omit_workbook=False) -> bytes:
    """A structurally minimal .xlsx. Not openpyxl-readable — that is not the point."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        if not omit_workbook:
            zf.writestr("xl/workbook.xml", "<workbook/>")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _check(file):
    return validate_upload(file, allowed_extensions=SPREADSHEETS, max_bytes=5 * ONE_MB)


# ----------------------------------------------------------- the decision


def test_the_declared_content_type_cannot_buy_acceptance():
    """
    The regression that motivated this module.

    The previous check read the client's `Content-Type` and skipped itself when
    the header was absent. Here the header says "spreadsheet" and the extension
    says otherwise — the extension has to win, or the check is decorative.
    """
    with pytest.raises(UnsafeUploadError):
        _check(
            _upload(
                "payload.exe",
                b"MZ\x90\x00",
                content_type=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
            )
        )


def test_a_missing_content_type_does_not_skip_the_check():
    """The old check was `if content_type and ...` — absent header, no check."""
    with pytest.raises(UnsafeUploadError):
        _check(_upload("payload.exe", b"MZ\x90\x00", content_type=""))


def test_the_declared_content_type_is_still_recorded():
    """Not trusted, but kept — an audit trail wants what the client claimed."""
    facts = _check(_upload("people.csv", b"name,phone\nA,9000000000\n", "text/lies"))

    assert facts.declared_content_type == "text/lies"
    assert facts.kind == "csv"


# ------------------------------------------------------------ containers


def test_a_macro_workbook_is_refused_even_when_renamed_to_xlsx():
    """
    `.xlsm` renamed to `.xlsx` is the obvious bypass, so the check is on the
    zip contents rather than on the name.
    """
    payload = _xlsx_bytes({"xl/vbaProject.bin": b"\x00macro"})

    with pytest.raises(UnsafeUploadError) as exc:
        _check(_upload("macros.xlsx", payload))

    assert "macro" in str(exc.value).lower()


def test_the_xlsm_extension_is_not_accepted_at_all():
    with pytest.raises(UnsafeUploadError):
        _check(_upload("book.xlsm", _xlsx_bytes()))


def test_a_zip_that_is_not_a_workbook_is_refused():
    with pytest.raises(UnsafeUploadError):
        _check(_upload("notreally.xlsx", _xlsx_bytes(omit_workbook=True)))


def test_a_file_that_is_not_a_zip_at_all_is_refused():
    with pytest.raises(UnsafeUploadError):
        _check(_upload("plain.xlsx", b"this is just text, not a container"))


def test_a_zip_bomb_is_refused_before_anything_decompresses_it():
    """
    Highly compressible filler: small on the wire, enormous expanded. The budget
    has to be checked from the zip directory, because by the time a parser has
    decompressed it the damage is done.
    """
    payload = _xlsx_bytes({"xl/worksheets/sheet1.xml": b"\x00" * (80 * ONE_MB)})

    with pytest.raises(UnsafeUploadError) as exc:
        _check(_upload("bomb.xlsx", payload))

    assert "expands" in str(exc.value).lower()


def test_a_normal_workbook_passes():
    facts = _check(_upload("candidates.xlsx", _xlsx_bytes()))

    assert facts.kind == "xlsx"
    assert facts.sha256
    assert facts.decompressed_bytes > 0


# ------------------------------------------------------------------ text


def test_a_binary_pretending_to_be_csv_is_refused():
    with pytest.raises(UnsafeUploadError):
        _check(_upload("data.csv", b"\x00\x01\x02\x03" * 500))


def test_a_csv_delimiter_is_detected_from_the_header():
    facts = _check(_upload("people.csv", "name;phone\nA;9000000000\n".encode()))

    assert facts.delimiter == ";"


def test_a_utf8_bom_is_handled():
    """Naukri and WorkIndia both export CSV with a BOM."""
    facts = _check(_upload("people.csv", "name,phone\nA,9000000000\n".encode("utf-8-sig")))

    assert facts.encoding == "utf-8-sig"


# ----------------------------------------------------------------- sizes


def test_an_empty_file_is_refused():
    with pytest.raises(UnsafeUploadError):
        _check(_upload("empty.csv", b""))


def test_an_oversized_file_is_refused():
    with pytest.raises(UnsafeUploadError) as exc:
        validate_upload(
            _upload("big.csv", b"a,b\n" * ONE_MB),
            allowed_extensions=SPREADSHEETS,
            max_bytes=1024,
        )

    assert "limit is" in str(exc.value)


# -------------------------------------------------------------- filenames


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("name\x00.csv", "name.csv"),
        ("line\r\ninjection.csv", "line injection.csv"),
        ("", "upload"),
    ],
)
def test_filenames_are_stripped_of_paths_and_control_characters(raw, expected):
    assert safe_original_name(raw) == expected


def test_a_very_long_filename_is_truncated():
    assert len(safe_original_name("a" * 500 + ".csv")) == 120


# ------------------------------------------------- formula injection (emit)


@pytest.mark.parametrize("dangerous", ["=cmd|'/c calc'!A0", "+1+1", "-1+1", "@SUM(A1)"])
def test_formula_leaders_are_neutralised_on_the_way_out(dangerous):
    """
    We never execute a formula on read. This guards the other direction: a value
    we emit into a CSV must not become a formula in whatever opens it.
    """
    assert neutralise_cell(dangerous).startswith("'")


def test_ordinary_values_are_left_alone():
    assert neutralise_cell("Priya Deshmukh") == "Priya Deshmukh"
    assert neutralise_cell("") == ""


def test_the_compression_ratio_guard_is_a_real_number():
    """A budget nobody can state is a budget nobody enforces."""
    assert MAX_COMPRESSION_RATIO > 1
