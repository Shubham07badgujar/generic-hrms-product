"""
WorkIndia — blue-collar hiring, phone-first.

The defining trait: rows routinely carry a phone number and NO email. That is
why `Candidate.email` is nullable and why phone participates in matching at
all. No address is ever invented to fill the gap.

Its export also ships age, gender and English-speaking proficiency. None is
mapped. We have no lawful reason to hold a candidate's age or gender for a
hiring decision, and carrying them into the domain tables would be collecting
data because it was offered rather than because it was needed.
"""

from __future__ import annotations

from .parsers import days, decimal_years, money
from .registry import ColumnSpec, PlatformSpec

WORKINDIA = PlatformSpec(
    key="workindia",
    label="WorkIndia",
    columns=(
        ColumnSpec("external_id", ("candidate id", "candidate code", "wi id", "id")),
        ColumnSpec("full_name", ("candidate name", "name", "full name")),
        ColumnSpec("first_name", ("first name", "firstname")),
        ColumnSpec("last_name", ("last name", "lastname", "surname")),
        # "mobile no" is what the current dashboard export actually writes
        # ("Mobile No." before punctuation is normalised away) — without it
        # the phone column silently went unmapped, which on a phone-first
        # platform means no identity for dedup at all.
        ColumnSpec("phone", ("phone", "mobile", "mobile no", "mobile number", "phone number", "contact", "contact number")),
        ColumnSpec("email", ("email", "email id", "email address")),
        ColumnSpec("current_employer", ("current company", "company", "employer", "current employer", "previous company name")),
        # "Relevant Experience" is the real export's numeric-ish column;
        # "Level Of Experience" beside it holds words like "Experienced" and
        # is deliberately not aliased here, so it can never shadow it.
        ColumnSpec("total_experience_years", ("experience", "total experience", "work experience", "relevant experience"), decimal_years),
        ColumnSpec("expected_ctc", ("expected salary", "expected ctc", "salary expectation"), money),
        ColumnSpec("notice_period_days", ("notice period", "availability"), days),
    ),
    # Phone-first: a WorkIndia row without a name is unusable, but one without
    # an email is completely ordinary.
    required=frozenset({"first_name"}),
    # Job-relevant extras -> Candidate.profile, matching the real dashboard
    # export's columns. Age, gender and the course timetable stay out.
    profile_columns=(
        ColumnSpec("city", ("city",)),
        ColumnSpec("area", ("location",)),
        ColumnSpec("qualification", ("qualification",)),
        ColumnSpec("course", ("course",)),
        ColumnSpec("specialization", ("specialization",)),
        ColumnSpec("college", ("college name",)),
        ColumnSpec("skills", ("skills",)),
        ColumnSpec("languages", ("languages known",)),
        ColumnSpec("english", ("english speaking",)),
        ColumnSpec("current_salary", ("current salary",)),
        ColumnSpec("designation", ("previous designation",)),
        ColumnSpec("experience_level", ("level of experience",)),
        ColumnSpec("resume_link", ("resume link",)),
    ),
    notes=(
        "Export from the WorkIndia employer dashboard using 'Export to Excel'. "
        "Contacts must be unlocked before phone numbers appear. Age and gender "
        "columns are ignored on import."
    ),
)
