"""
WorkIndia, against the REAL dashboard export shape.

Headers verbatim from an actual August 2026 'Export to Excel' file; values
fabricated. The gap this pins: the export's City / Qualification / Skills /
Current Salary / Resume Link columns must reach the candidate's profile, and
"Relevant Experience" must feed the experience field — an import that drops
them shows a candidate card full of dashes.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.imports.services import importer
from apps.recruitment.models import Candidate

pytestmark = pytest.mark.django_db

WORKINDIA_REAL_HEADERS = [
    "Sr. No.", "Full Name", "Mobile No.", "City", "Location", "Applied At",
    "Qualification", "Level Of Experience", "Relevant Experience", "Gender",
    "Resume Link", "Profile Link", "Languages Known", "Skills",
    "Current Salary", "English Speaking", "Age", "Course", "Specialization",
    "College Name", "Course Start Time", "Course End Time",
    "Previous Designation", "Previous Company Name", "Last Active",
]


def _row():
    by_header = {
        "Sr. No.": "1",
        "Full Name": "Malhar Example",
        "Mobile No.": "9876543240",
        "City": "Mumbai",
        "Location": "Kandivali",
        "Applied At": "26 Aug 2026",
        "Qualification": "Graduate",
        "Level Of Experience": "Experienced",
        "Relevant Experience": "2 Years",
        "Gender": "Male",
        "Resume Link": "https://workindia.example.test/resume/1",
        "Languages Known": "Hindi, Marathi, English",
        "Skills": "Customer Handling, Tele-calling",
        "Current Salary": "15000",
        "English Speaking": "Good",
        "Age": "24",
        "Course": "B.Com",
        "College Name": "Example College",
        "Previous Designation": "Telecaller",
        "Previous Company Name": "Old Firm",
        "Last Active": "Today",
    }
    return [by_header.get(header, "") for header in WORKINDIA_REAL_HEADERS]


def test_the_real_workindia_export_fills_the_candidate_card(
    attested_batch, upload, importer_user
):
    from tests.imports.conftest import _xlsx

    batch = attested_batch(upload("workindia_real.xlsx", _xlsx(WORKINDIA_REAL_HEADERS, [_row()])))
    assert {"full_name", "phone", "current_employer", "total_experience_years"} <= set(
        batch.column_mapping
    )

    importer.commit(actor=importer_user, batch=batch)

    candidate = Candidate.objects.get(first_name="Malhar")
    assert candidate.source == "workindia"
    assert candidate.total_experience_years == Decimal("2.0")  # Relevant Experience
    assert candidate.current_employer == "Old Firm"

    profile = candidate.profile
    assert profile["city"] == "Mumbai"
    assert profile["qualification"] == "Graduate"
    assert profile["skills"] == "Customer Handling, Tele-calling"
    assert profile["current_salary"] == "15000"
    assert profile["resume_link"].startswith("https://")
    assert profile["college"] == "Example College"
    # Age and gender ship in the file and must never reach the candidate.
    blob = " ".join(f"{k}={v}" for k, v in profile.items()).lower()
    assert "male" not in blob.replace("female", "")
    assert "age" not in profile
