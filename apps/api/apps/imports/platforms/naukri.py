"""
Naukri.com — white-collar hiring, email-first.

Verified against real Response Management exports (August 2026): ~79 columns,
one row per applicant, "NA" written into every cell the platform has nothing
for. Rows almost always carry an email; experience arrives as
"5 Year(s) 6 Month(s)" (or "Fresher"), salary in lakh shorthand
("Rs 9.60 Lakhs"), and the expected CTC lives in a per-job screening question
("Ans(What is your expected CTC in Lacs per annum?)") rather than a fixed
column.

The export also ships Gender, Marital Status, Date of Birth, and the full
permanent address. None is mapped — we have no lawful reason to hold any of
them for a hiring decision, and the curated `profile_columns` list is the
mechanism that keeps them out.
"""

from __future__ import annotations

from .parsers import clean_text, days, decimal_years, money
from .registry import ColumnSpec, PlatformSpec

NAUKRI = PlatformSpec(
    key="naukri",
    label="Naukri.com",
    columns=(
        ColumnSpec("external_id", ("applicant id", "candidate id", "profile id", "resume id")),
        # The real export's name column is simply "Name".
        ColumnSpec("full_name", ("candidate name", "name", "applicant name")),
        ColumnSpec("first_name", ("first name", "firstname")),
        ColumnSpec("last_name", ("last name", "lastname", "surname")),
        ColumnSpec("email", ("email", "email id", "email address", "candidate email")),
        ColumnSpec("phone", ("mobile", "phone", "phone number", "mobile number", "contact number")),
        # "Curr. Company name" survives header normalisation as
        # "curr company name". Cleaned so "NA" never becomes an employer.
        ColumnSpec(
            "current_employer",
            ("current employer", "current company", "curr company name",
             "organisation", "organization"),
            clean_text,
        ),
        ColumnSpec("total_experience_years", ("total experience", "experience", "exp"), decimal_years),
        # The fixed "Annual Salary" column is the CURRENT salary — the
        # expected CTC only exists as a screening question, whose header the
        # dashboard writes verbatim.
        ColumnSpec(
            "expected_ctc",
            ("expected ctc", "expected salary", "expected",
             "ans what is your expected ctc in lacs per annum"),
            money,
        ),
        ColumnSpec(
            "notice_period_days",
            ("notice period", "notice", "notice period availability to join"),
            days,
        ),
    ),
    required=frozenset({"first_name"}),
    # Job-relevant extras -> Candidate.profile. Everything else in the export
    # stays in the staged row's raw cells and goes no further.
    profile_columns=(
        ColumnSpec("city", ("current location",)),
        ColumnSpec("preferred_locations", ("preferred locations",)),
        ColumnSpec("designation", ("curr company designation",)),
        ColumnSpec("qualification", ("under graduation degree",)),
        ColumnSpec("pg_qualification", ("post graduation degree",)),
        ColumnSpec("skills", ("key skills",)),
        ColumnSpec("industry", ("industry",)),
        ColumnSpec("resume_headline", ("resume headline",)),
        ColumnSpec(
            "current_salary",
            ("annual salary", "ans what is your current ctc in lacs per annum"),
        ),
    ),
    notes=(
        "Export from Naukri Response Management ('Download Excel'). Requires a "
        "paid recruiter account. Both .xlsx and .csv exports are accepted; "
        "'NA' cells are treated as empty."
    ),
)
