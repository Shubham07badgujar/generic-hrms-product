"""
The shapes real platform exports actually arrive in.

NO GENUINE EXPORT FILES EXIST in this repository or environment — I checked.
Every fixture here is synthesised to reproduce the documented column structure
and the formatting quirks the adapters were written against, with invented
people: `.test` addresses (RFC 6761) and the documentation number range.

That is a real limitation and it is stated in the UAT report rather than
papered over: these prove the parser handles the shapes we BELIEVE the exports
have. Only a genuine file proves the belief.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest

from apps.imports.platforms import NAUKRI, WORKINDIA
from apps.imports.services.parsing import parse
from core.validators import validate_upload

pytestmark = pytest.mark.django_db

EXTENSIONS = frozenset({".xlsx", ".csv"})

WORKINDIA_HEADERS = [
    "Candidate ID", "Candidate Name", "Mobile Number", "Email",
    "Current Company", "Experience", "Expected Salary", "Notice Period",
    "Age", "Gender", "English",
]

NAUKRI_HEADERS = [
    "Applicant ID", "Candidate Name", "Email ID", "Mobile",
    "Current Employer", "Total Experience", "Expected CTC", "Notice Period",
]


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


def _text(headers, rows, *, delimiter=",", encoding="utf-8") -> bytes:
    lines = [delimiter.join(str(h) for h in headers)]
    lines += [delimiter.join("" if v is None else str(v) for v in row) for row in rows]
    return "\n".join(lines).encode(encoding)


def _parse(file, spec):
    facts = validate_upload(file, allowed_extensions=EXTENSIONS, max_bytes=5 * 1024 * 1024)
    return parse(file, facts=facts, spec=spec)


def _by_name(parsed, first_name):
    return next(r for r in parsed.rows if r.get("first_name") == first_name)


# ------------------------------------------------------------- WorkIndia

#: The formatting variety a single WorkIndia export genuinely contains.
WORKINDIA_ROWS = [
    # Fully populated.
    ["WI-1001", "Priya Deshmukh", "9876543210", "priya@example.test",
     "City Physio", "4.5", "6.5 lakh", "30 days", "29", "F", "Good"],
    # Phone only — no email at all. The case the whole feature exists for.
    ["WI-1002", "Ramesh Kumar", "098765 43211", "",
     "", "2 years", "", "Immediate", "34", "M", "Fluent"],
    # +91 prefix, years-and-months, crore shorthand, month notice.
    ["WI-1003", "Anita Joshi", "+91 98765 43212", "anita@example.test",
     "Clinic Co", "8 years 6 months", "1.2 crore", "1 month", "31", "F", "Good"],
    # Almost everything blank but still identifiable.
    ["WI-1004", "Sunil Rao", "9876543213", "", "", "", "", "", "", "", ""],
    # Exact duplicate of the first row — exports produce these constantly.
    ["WI-1001", "Priya Deshmukh", "9876543210", "priya@example.test",
     "City Physio", "4.5", "6.5 lakh", "30 days", "29", "F", "Good"],
]


@pytest.mark.parametrize("kind", ["xlsx", "csv"])
def test_a_representative_workindia_export_parses(upload, kind):
    payload = (
        _book(WORKINDIA_HEADERS, WORKINDIA_ROWS) if kind == "xlsx"
        else _text(WORKINDIA_HEADERS, WORKINDIA_ROWS)
    )
    parsed = _parse(upload(f"workindia.{kind}", payload), WORKINDIA)

    assert len(parsed.rows) == 5

    priya = _by_name(parsed, "Priya")
    assert priya["last_name"] == "Deshmukh"
    assert priya["email"] == "priya@example.test"
    assert priya["external_id"] == "WI-1001"
    assert priya["total_experience_years"] == Decimal("4.5")
    assert priya["expected_ctc"] == Decimal("650000.00")
    assert priya["notice_period_days"] == 30


def test_the_phone_only_row_carries_no_email(upload):
    parsed = _parse(
        upload("wi.xlsx", _book(WORKINDIA_HEADERS, WORKINDIA_ROWS)), WORKINDIA
    )
    ramesh = _by_name(parsed, "Ramesh")

    assert not ramesh.get("email")
    assert ramesh["phone"] == "098765 43211"
    assert ramesh["notice_period_days"] == 0  # "Immediate"


def test_the_awkward_formats_all_land(upload):
    parsed = _parse(
        upload("wi.xlsx", _book(WORKINDIA_HEADERS, WORKINDIA_ROWS)), WORKINDIA
    )
    anita = _by_name(parsed, "Anita")

    assert anita["total_experience_years"] == Decimal("8.5")
    assert anita["expected_ctc"] == Decimal("12000000.00")
    assert anita["notice_period_days"] == 30


def test_blank_optional_values_become_absent_not_wrong(upload):
    parsed = _parse(
        upload("wi.xlsx", _book(WORKINDIA_HEADERS, WORKINDIA_ROWS)), WORKINDIA
    )
    sunil = _by_name(parsed, "Sunil")

    assert sunil["total_experience_years"] is None
    assert sunil["expected_ctc"] is None
    assert sunil["notice_period_days"] is None


def test_the_columns_we_will_not_hold_stay_unmapped(upload):
    parsed = _parse(
        upload("wi.xlsx", _book(WORKINDIA_HEADERS, WORKINDIA_ROWS)), WORKINDIA
    )

    for column in ("Age", "Gender", "English"):
        assert column in parsed.unmapped_headers
    assert not {"age", "gender", "english"} & set(parsed.mapping)


# ---------------------------------------------------------------- Naukri

NAUKRI_ROWS = [
    ["NK-500", "Sanjay Iyer", "sanjay@example.test", "+91 98765 43220",
     "Hospital Ltd", "8 years 6 months", "12 lakh", "90"],
    # Email only — no phone, the mirror of the WorkIndia case.
    ["NK-501", "Meera Kulkarni", "meera@example.test", "",
     "Care Group", "10", "1500000", "30"],
    # Mixed-case address; the importer lowercases on the way in.
    ["NK-502", "Vikram Malhotra", "VIKRAM@EXAMPLE.TEST", "9876543222",
     "", "3.5", "", "Immediate"],
]


@pytest.mark.parametrize("kind", ["xlsx", "csv"])
def test_a_representative_naukri_export_parses(upload, kind):
    payload = (
        _book(NAUKRI_HEADERS, NAUKRI_ROWS) if kind == "xlsx"
        else _text(NAUKRI_HEADERS, NAUKRI_ROWS)
    )
    parsed = _parse(upload(f"naukri.{kind}", payload), NAUKRI)

    assert len(parsed.rows) == 3
    sanjay = _by_name(parsed, "Sanjay")
    assert sanjay["email"] == "sanjay@example.test"
    assert sanjay["external_id"] == "NK-500"
    assert sanjay["total_experience_years"] == Decimal("8.5")
    assert sanjay["expected_ctc"] == Decimal("1200000.00")
    assert sanjay["notice_period_days"] == 90


def test_an_email_only_naukri_row_parses(upload):
    parsed = _parse(upload("nk.xlsx", _book(NAUKRI_HEADERS, NAUKRI_ROWS)), NAUKRI)
    meera = _by_name(parsed, "Meera")

    assert meera["email"] == "meera@example.test"
    assert not meera.get("phone")


# ------------------------------------------------------ header variations


@pytest.mark.parametrize(
    "headers",
    [
        ["candidate_id", "candidate_name", "mobile_number", "email"],
        ["CANDIDATE ID", "CANDIDATE NAME", "MOBILE NUMBER", "EMAIL"],
        ["Candidate  ID", "Candidate  Name", "Mobile  Number", "Email"],
        ["Candidate-ID", "Candidate-Name", "Mobile-Number", "Email"],
        ["Candidate ID ", " Candidate Name", "Mobile Number ", " Email "],
    ],
)
def test_header_spelling_variations_all_resolve(upload, headers):
    """
    The same dashboard emits different capitalisation and separators between
    versions, and a stray double space is invisible to whoever exported it.
    """
    payload = _book(headers, [["WI-1", "Priya Deshmukh", "9876543210", "p@example.test"]])
    parsed = _parse(upload("v.xlsx", payload), WORKINDIA)

    row = parsed.rows[0]
    assert row["first_name"] == "Priya"
    assert row["external_id"] == "WI-1"
    assert row["phone"] == "9876543210"


def test_an_unexpected_extra_column_is_ignored_not_fatal(upload):
    """A platform adding a column must not break every existing import."""
    headers = WORKINDIA_HEADERS + ["Newly Added Column"]
    payload = _book(headers, [WORKINDIA_ROWS[0] + ["something"]])

    parsed = _parse(upload("extra.xlsx", payload), WORKINDIA)

    assert parsed.rows[0]["first_name"] == "Priya"
    assert "Newly Added Column" in parsed.unmapped_headers


def test_a_missing_optional_column_is_not_fatal(upload):
    """Exports vary by subscription tier; absent columns are simply absent."""
    headers = ["Candidate ID", "Candidate Name", "Mobile Number"]
    payload = _book(headers, [["WI-1", "Priya Deshmukh", "9876543210"]])

    parsed = _parse(upload("thin.xlsx", payload), WORKINDIA)

    assert parsed.rows[0]["first_name"] == "Priya"
    assert not parsed.rows[0].get("email")
