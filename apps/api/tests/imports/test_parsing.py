"""
Reading platform exports, including ones written to attack the reader.

Covers both supported platforms in both formats, alias matching, manual column
mapping, and every hostile-input control.
"""

from __future__ import annotations

import io

import pytest

from apps.imports.platforms import NAUKRI, WORKINDIA, get_platform, list_platforms
from apps.imports.services.parsing import MAX_CELL_CHARS, ParseError, parse
from core.validators import UnsafeUploadError, validate_upload

pytestmark = pytest.mark.django_db

EXTENSIONS = frozenset({".xlsx", ".csv"})


def _facts(file):
    return validate_upload(
        file, allowed_extensions=EXTENSIONS, max_bytes=5 * 1024 * 1024
    )


def _parse(file, spec=WORKINDIA, **kwargs):
    return parse(file, facts=_facts(file), spec=spec, **kwargs)


def _book(headers, rows) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------- both formats


def test_workindia_xlsx_parses(workindia_xlsx):
    parsed = _parse(workindia_xlsx())

    assert len(parsed.rows) == 2
    first = parsed.rows[0]
    assert first["first_name"] == "Priya"
    assert first["last_name"] == "Deshmukh"
    assert first["email"] == "priya@example.test"
    assert first["external_id"] == "WI-1001"


def test_workindia_csv_parses(workindia_csv):
    assert _parse(workindia_csv()).rows[0]["first_name"] == "Anita"


def test_naukri_xlsx_parses(naukri_xlsx):
    row = _parse(naukri_xlsx(), spec=NAUKRI).rows[0]

    assert row["first_name"] == "Sanjay"
    assert row["email"] == "sanjay@example.test"


def test_naukri_csv_parses(naukri_csv):
    assert _parse(naukri_csv(), spec=NAUKRI).rows[0]["first_name"] == "Meera"


def test_a_semicolon_delimited_csv_parses(workindia_csv):
    assert _parse(workindia_csv(delimiter=";")).rows[0]["first_name"] == "Anita"


def test_a_bom_encoded_csv_parses(workindia_csv):
    """Both platforms emit a BOM."""
    assert _parse(workindia_csv(encoding="utf-8-sig")).rows[0]["first_name"] == "Anita"


# ----------------------------------------------------------------- mapping


def test_headers_match_case_and_punctuation_insensitively(upload):
    """Exports vary between "Mobile Number", "mobile_number" and "Mobile  Number"."""
    payload = _book(
        ["candidate_id", "CANDIDATE  NAME", "mobile-number"],
        [["WI-1", "Priya Deshmukh", "9876543210"]],
    )
    parsed = _parse(upload("odd-headers.xlsx", payload))

    assert parsed.rows[0]["first_name"] == "Priya"
    assert parsed.rows[0]["external_id"] == "WI-1"


def test_a_manual_column_override_wins(upload):
    payload = _book(["Thing", "Number"], [["Priya Deshmukh", "9876543210"]])

    parsed = _parse(
        upload("unknown.xlsx", payload),
        column_override={"Thing": "full_name", "Number": "phone"},
    )

    assert parsed.rows[0]["first_name"] == "Priya"


def test_a_file_with_no_recognisable_columns_is_refused(upload):
    payload = _book(["alpha", "beta"], [["1", "2"]])

    with pytest.raises(ParseError):
        _parse(upload("nothing.xlsx", payload))


def test_columns_we_have_no_reason_to_hold_are_not_mapped(workindia_xlsx):
    """
    WorkIndia ships age, gender and English proficiency.

    None is mapped. We have no lawful reason to hold a candidate's age or
    gender for a hiring decision, and carrying them into the domain tables
    would be collecting data because it was offered, not because it was needed.
    """
    parsed = _parse(workindia_xlsx())

    assert "Age" in parsed.unmapped_headers
    assert "Gender" in parsed.unmapped_headers
    assert "age" not in parsed.mapping
    assert "gender" not in parsed.mapping


# ------------------------------------------------------------ value parsing


@pytest.mark.parametrize(
    "raw,expected", [("6.5 lakh", 650000), ("500000", 500000), ("1.2 crore", 12000000)]
)
def test_salary_shorthand_is_understood(raw, expected):
    from decimal import Decimal

    from apps.imports.platforms.parsers import money

    assert money(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw,expected", [("30 days", 30), ("1 month", 30), ("Immediate", 0)]
)
def test_notice_period_is_understood(raw, expected):
    from apps.imports.platforms.parsers import days

    assert days(raw) == expected


def test_experience_in_years_and_months():
    from decimal import Decimal

    from apps.imports.platforms.parsers import decimal_years

    assert decimal_years("8 years 6 months") == Decimal("8.5")
    assert decimal_years("4.5") == Decimal("4.5")


# --------------------------------------------------------------- hostile


def test_a_macro_workbook_is_refused(upload, malicious):
    with pytest.raises(UnsafeUploadError) as exc:
        _facts(upload("macros.xlsx", malicious["macro"]))

    assert "macro" in str(exc.value).lower()


def test_a_zip_bomb_is_refused(upload, malicious):
    with pytest.raises(UnsafeUploadError):
        _facts(upload("bomb.xlsx", malicious["zip_bomb"]))


def test_a_non_workbook_is_refused(upload, malicious):
    with pytest.raises(UnsafeUploadError):
        _facts(upload("fake.xlsx", malicious["not_a_zip"]))


def test_a_binary_csv_is_refused(upload, malicious):
    with pytest.raises(UnsafeUploadError):
        _facts(upload("binary.csv", malicious["csv_with_nulls"]))


def test_a_formula_never_reaches_a_candidate_field(formula_xlsx):
    """
    `data_only=True` is a security control, not a convenience.

    Without it the cell reads back as literal text and a shell-command payload
    lands in first_name. With it, openpyxl returns the value Excel cached — and
    openpyxl writes no cache, so the correct outcome is an empty name, which the
    importer then rejects rather than importing.
    """
    parsed = _parse(formula_xlsx())

    assert "cmd" not in str(parsed.rows[0])
    assert not parsed.rows[0].get("first_name")


def test_an_enormous_cell_is_truncated(upload):
    payload = _book(
        ["Candidate Name", "Mobile Number"], [["A" * 50_000, "9876543210"]]
    )

    parsed = _parse(upload("huge-cell.xlsx", payload))

    assert len(parsed.rows[0]["first_name"]) <= MAX_CELL_CHARS


def test_an_unavailable_platform_cannot_be_parsed(workindia_xlsx):
    """LinkedIn has no export to parse; the adapter says so rather than trying."""
    from apps.imports.platforms import LINKEDIN

    with pytest.raises(ParseError):
        _parse(workindia_xlsx(), spec=LINKEDIN)


# -------------------------------------------------------------- registry


def test_the_registry_reports_availability_and_reasons():
    keys = {spec.key for spec in list_platforms()}

    assert {"workindia", "naukri", "internshala", "indeed", "linkedin"} <= keys
    assert get_platform("internshala").available is True
    for key in ("indeed", "linkedin"):
        spec = get_platform(key)
        assert spec.available is False
        assert spec.unavailable_reason, f"{key} must say WHY it is unavailable"


def test_available_platforms_come_first():
    availability = [spec.available for spec in list_platforms()]

    assert availability == sorted(availability, reverse=True)


# ------------------------------------------------------- mapping safety


def test_a_mapping_cannot_name_an_arbitrary_model_field(upload):
    """
    The control that makes manual mapping safe to expose.

    Downstream, `_normalise_row` reads a fixed set of keys and would ignore
    anything else — but relying on that is defence by accident. An override
    naming a field outside CANONICAL_FIELDS is refused outright.
    """
    payload = _book(["Thing"], [["Priya Deshmukh"]])

    for target in ("consent_given", "is_active", "legal_basis", "id", "__class__"):
        with pytest.raises(ParseError) as exc:
            _parse(upload("m.xlsx", payload), column_override={"Thing": target})
        assert "column_override" in str(exc.value)


def test_a_mapping_to_a_canonical_field_is_accepted(upload):
    payload = _book(["Thing", "Number"], [["Priya Deshmukh", "9876543210"]])

    parsed = _parse(
        upload("m.xlsx", payload),
        column_override={"Thing": "full_name", "Number": "phone"},
    )

    assert parsed.rows[0]["first_name"] == "Priya"
    assert parsed.rows[0]["phone"] == "9876543210"


def test_an_unrecognised_file_reports_its_headers_for_mapping(upload):
    """
    Without the headers, the caller can only be told "unrecognised", which
    dead-ends an otherwise valid export whose column names simply differ.
    """
    payload = _book(["Naam", "Number"], [["Priya", "9876543210"]])

    with pytest.raises(ParseError) as exc:
        _parse(upload("unknown.xlsx", payload))

    detail = exc.value.message_dict
    assert detail["detected_headers"] == ["Naam", "Number"]


def test_the_batch_records_which_headers_nothing_claimed(
    importer_user, import_job, workindia_xlsx
):
    """So the preview can offer to map the leftovers."""
    from apps.imports.platforms import WORKINDIA
    from apps.imports.services import importer

    batch = importer.create_batch(
        actor=importer_user, platform=WORKINDIA,
        job_opening=import_job, file=workindia_xlsx(),
    )

    assert "Candidate Name" in batch.detected_headers
    assert "Age" in batch.unmapped_headers
