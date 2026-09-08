"""
Naukri.com and Internshala, against their REAL export shapes.

Header lists here are copied verbatim from actual August 2026 exports
(Response Management for Naukri, the employer dashboard for Internshala);
every candidate value is fabricated. The claims:

  * the real headers map without any manual column override
  * "NA" cells and "Fresher"/"Year(s)" spellings parse instead of polluting
  * committed candidates carry the right `source` and a curated `profile`
  * protected attributes the exports ship (gender, marital status, DOB)
    never reach the candidate
  * a re-import of the same file creates nothing new
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.imports.platforms import INTERNSHALA, NAUKRI
from apps.imports.services import importer
from apps.recruitment.models import Candidate, CandidateExternalRef

pytestmark = pytest.mark.django_db

# The full header row of a real Response Management export, verbatim.
NAUKRI_REAL_HEADERS = [
    "Job Title", "Date of application", "Name", "Email ID", "Phone Number",
    "Current Location", "Preferred Locations", "Total Experience",
    "Curr. Company name", "Curr. Company Designation", "Department", "Role",
    "Industry", "Key Skills", "Annual Salary",
    "Notice period/ Availability to join", "Resume Headline", "Summary",
    "Under Graduation degree", "UG Specialization",
    "UG University/institute Name", "UG Graduation year",
    "Post graduation degree", "PG specialization",
    "PG university/institute name", "PG graduation year", "Doctorate degree",
    "Doctorate specialization", "Doctorate university/institute name",
    "Doctorate graduation year", "Gender", "Marital Status", "Home Town/City",
    "Pin Code", "Work permit for USA", "Date of Birth", "Permanent Address",
    "Last Workflow activity", "Last Workflow activity by",
    "Time of Last Workflow activity Update", "Latest Pipeline Stage",
    "Pipeline Status Updated By", "Time when Stage updated", "Download",
    "Downloaded By", "Time Of Download", "Viewed", "Viewed By", "Time Of View",
    "Emailed", "Emailed By", "Time Of Email", "Calling Status",
    "Calling Status updated by", "Time of Calling activity update",
    "Comment 1", "Comment 1 BY", "Time Comment 1 posted",
    "Comment 2", "Comment 2 BY", "Time Comment 2 posted",
    "Comment 3", "Comment 3 BY", "Time Comment 3 posted",
    "Comment 4", "Comment 4 BY", "Time Comment 4 posted",
    "Comment 5", "Comment 5 BY", "Time Comment 5 posted", "Source",
    "Ans(How many years of experience do you have in SEO?)",
    "Ans(Which strategy provides faster results?)",
    "Ans(How many years of experience do you have in Digital Marketing?)",
    "Ans(What is your expected CTC in Lacs per annum?)",
    "Ans(What is your current CTC in Lacs per annum?)",
    "Ans(What is your notice period?)",
    "Ans(Which is an example of organic marketing?)",
    "Candidate profile",
]


def _naukri_row(
    name="Asha Vaidya", email="asha@example.test", phone="9876543220",
    experience="3 Year(s) 6 Month(s)", employer="Care Clinic",
    expected="600000",
):
    """One row, padded with 'NA' exactly the way the dashboard writes it."""
    by_header = {
        "Job Title": "Digital Marketing Executive",
        "Date of application": "20-Aug-2026",
        "Name": name,
        "Email ID": email,
        "Phone Number": phone,
        "Current Location": "Mumbai",
        "Preferred Locations": "Mumbai, Thane",
        "Total Experience": experience,
        "Curr. Company name": employer,
        "Curr. Company Designation": "Executive",
        "Key Skills": "SEO, Content",
        "Annual Salary": "Rs 3.20 Lakhs",
        "Notice period/ Availability to join": "More than 3 Months",
        "Under Graduation degree": "B.B.A. / B.M.S.",
        "Gender": "Female",
        "Marital Status": "Single/unmarried",
        "Date of Birth": "28-Nov-2001",
        "Ans(What is your expected CTC in Lacs per annum?)": expected,
        "Ans(What is your current CTC in Lacs per annum?)": "320000",
    }
    return [by_header.get(header, "NA") for header in NAUKRI_REAL_HEADERS]


# Verbatim from a real Internshala applications export.
INTERNSHALA_REAL_HEADERS = [
    "Name", "Link to application", "Phone", "Email Address", "Current City",
    "Gender", "Social Media Marketing", "Digital Marketing", "Email Marketing",
    "Analytical Thinking", "MS-Excel", "Business Analysis", "Lead Generation",
    "B2B Sales", "English Proficiency (Spoken)",
    "English Proficiency (Written)", "Interpersonal skills", "Market research",
    "Effective Communication", "Other skills", "AI Resume Match",
    "AI Interview Score in Business Analysis (out of 10)",
    "AI Interview Score in Social Media Marketing (out of 10)",
    "Please confirm your availability for this internship. If not available "
    "immediately, how early would you be able to join?",
    "Institute", "Degree", "Stream", "Current Year Of Graduation",
    "Performance_PG", "Performance_UG", "Performance_12", "Performance_10",
    "Link to chat", "Uploaded Resume/CV link", "Link to download application",
    "Date of Application",
]


def _internshala_row(
    name="Kiran Bhosale", email="kiran@example.test", phone="9876543230",
    link="https://internshala.example.test/application/9001",
):
    by_header = {
        "Name": name,
        "Link to application": link,
        "Phone": phone,
        "Email Address": email,
        "Current City": "Thane",
        "Gender": "Female",
        "MS-Excel": "yes",
        "Other skills": "Client Interaction, Problem Solving",
        "AI Resume Match": "Good",
        "Please confirm your availability for this internship. If not "
        "available immediately, how early would you be able to join?":
            "Yes, I am available to join immediately.",
        "Institute": "Example College",
        "Degree": "B.Sc",
        "Stream": "Computer Science",
        "Current Year Of Graduation": "2026",
        "Performance_UG": "N/A",
        "Uploaded Resume/CV link": "https://internshala.example.test/resume/9001",
        "Date of Application": "24-08-2026 03:31:05",
    }
    return [by_header.get(header) for header in INTERNSHALA_REAL_HEADERS]


@pytest.fixture
def naukri_real_xlsx(upload):
    def _make(rows=None, name="Digital-Marketing-Ex_20260826_2.xlsx"):
        from tests.imports.conftest import _xlsx

        rows = rows if rows is not None else [
            _naukri_row(),
            _naukri_row(
                name="Naitik Fresher", email="naitik@example.test",
                phone="9876543221", experience="Fresher", employer="NA",
                expected="NA",
            ),
        ]
        return upload(name, _xlsx(NAUKRI_REAL_HEADERS, rows))

    return _make


@pytest.fixture
def internshala_xlsx(upload):
    def _make(rows=None, name="Internshala_open_Applications.xlsx"):
        from tests.imports.conftest import _xlsx

        rows = rows if rows is not None else [_internshala_row()]
        return upload(name, _xlsx(INTERNSHALA_REAL_HEADERS, rows))

    return _make


# ------------------------------------------------------------- Naukri.com


def test_the_real_naukri_export_maps_without_a_manual_override(
    attested_batch, naukri_real_xlsx
):
    batch = attested_batch(naukri_real_xlsx(), platform=NAUKRI)
    mapped = set(batch.column_mapping)
    assert {
        "full_name", "email", "phone", "current_employer",
        "total_experience_years", "expected_ctc", "notice_period_days",
    } <= mapped
    # All 79 columns survive into the staged preview.
    assert len(batch.detected_headers) == len(NAUKRI_REAL_HEADERS)


def test_naukri_values_parse_the_way_the_dashboard_writes_them(
    attested_batch, naukri_real_xlsx, importer_user
):
    batch = attested_batch(naukri_real_xlsx(), platform=NAUKRI)
    importer.commit(actor=importer_user, batch=batch)

    asha = Candidate.objects.get(email="asha@example.test")
    assert asha.source == "naukri"
    assert asha.total_experience_years == Decimal("3.5")   # "3 Year(s) 6 Month(s)"
    assert asha.expected_ctc == Decimal("600000.00")       # the screening answer
    assert asha.notice_period_days == 90                   # "More than 3 Months"
    assert asha.current_employer == "Care Clinic"

    fresher = Candidate.objects.get(email="naitik@example.test")
    assert fresher.total_experience_years is None          # "Fresher"
    assert fresher.expected_ctc is None                    # "NA"
    assert fresher.current_employer == ""                  # "NA", not the text


def test_naukri_extras_land_in_the_profile_and_protected_fields_do_not(
    attested_batch, naukri_real_xlsx, importer_user
):
    batch = attested_batch(naukri_real_xlsx(), platform=NAUKRI)
    importer.commit(actor=importer_user, batch=batch)

    profile = Candidate.objects.get(email="asha@example.test").profile
    assert profile["city"] == "Mumbai"
    assert profile["skills"] == "SEO, Content"
    assert profile["qualification"] == "B.B.A. / B.M.S."
    assert profile["current_salary"] == "Rs 3.20 Lakhs"
    assert profile["designation"] == "Executive"

    blob = " ".join(f"{k}={v}" for k, v in profile.items()).lower()
    for forbidden in ("female", "single/unmarried", "28-nov-2001"):
        assert forbidden not in blob


def test_a_naukri_reimport_creates_nothing_new(
    attested_batch, naukri_real_xlsx, importer_user
):
    importer.commit(actor=importer_user, batch=attested_batch(naukri_real_xlsx(), platform=NAUKRI))
    before = Candidate.objects.count()

    again = attested_batch(naukri_real_xlsx(), platform=NAUKRI)
    result = importer.commit(actor=importer_user, batch=again)

    assert Candidate.objects.count() == before
    assert result.created == 0
    again.refresh_from_db()
    assert again.rows_created == 0
    assert again.rows_duplicate + again.rows_updated == 2


# ------------------------------------------------------------ Internshala


def test_the_real_internshala_export_maps_and_commits(
    attested_batch, internshala_xlsx, importer_user
):
    batch = attested_batch(internshala_xlsx(), platform=INTERNSHALA)
    assert {"full_name", "email", "phone", "external_id"} <= set(batch.column_mapping)

    result = importer.commit(actor=importer_user, batch=batch)
    assert result.created == 1

    kiran = Candidate.objects.get(email="kiran@example.test")
    assert kiran.source == "internshala"
    assert kiran.first_name == "Kiran"
    assert kiran.last_name == "Bhosale"
    assert kiran.phone_e164 == "+919876543230"

    profile = kiran.profile
    assert profile["city"] == "Thane"
    assert profile["institute"] == "Example College"
    assert profile["qualification"] == "B.Sc"
    assert profile["stream"] == "Computer Science"
    assert profile["graduation_year"] == "2026"
    assert profile["availability"].startswith("Yes, I am available")
    assert profile["resume_link"].startswith("https://")
    assert "gender" not in profile
    # "N/A" performance cells never become profile facts.
    assert "N/A" not in profile.values()

    # The application link is the platform's stable reference for dedup.
    assert CandidateExternalRef.objects.filter(
        source="internshala", candidate=kiran
    ).exists()


def test_an_internshala_reimport_is_recognised_by_the_application_link(
    attested_batch, internshala_xlsx, importer_user
):
    importer.commit(
        actor=importer_user, batch=attested_batch(internshala_xlsx(), platform=INTERNSHALA)
    )
    result = importer.commit(
        actor=importer_user, batch=attested_batch(internshala_xlsx(), platform=INTERNSHALA)
    )
    assert result.created == 0
    assert Candidate.objects.filter(email="kiran@example.test").count() == 1
