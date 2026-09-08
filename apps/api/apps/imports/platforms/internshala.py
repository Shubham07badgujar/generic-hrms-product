"""
Internshala — internships and early-career hiring.

Verified against a real employer-dashboard export (August 2026): one row per
applicant, phone and email always present, a block of per-skill columns
holding "yes"/blank, and education split across Institute / Degree / Stream /
graduation year. There is still no employer API — this adapter reads the
Premium dashboard's Excel export, which is exactly the file HR already
downloads.

The application URL is the platform's stable per-application identifier and
doubles as the external reference for dedup. Gender ships in the export and
is deliberately not mapped.
"""

from __future__ import annotations

from .registry import ColumnSpec, PlatformSpec

INTERNSHALA = PlatformSpec(
    key="internshala",
    label="Internshala",
    columns=(
        ColumnSpec("external_id", ("link to application", "application id", "applicant id")),
        ColumnSpec("full_name", ("name", "candidate name", "applicant name")),
        ColumnSpec("first_name", ("first name", "firstname")),
        ColumnSpec("last_name", ("last name", "lastname", "surname")),
        ColumnSpec("email", ("email address", "email", "email id")),
        ColumnSpec("phone", ("phone", "phone number", "mobile", "mobile number", "contact number")),
    ),
    required=frozenset({"first_name"}),
    # Job-relevant extras -> Candidate.profile. The per-skill yes/no columns
    # vary with every posting, so the free-text "Other skills" is the one
    # skills column mapped; the rest stay visible in the staged row.
    profile_columns=(
        ColumnSpec("city", ("current city",)),
        ColumnSpec("institute", ("institute",)),
        ColumnSpec("qualification", ("degree",)),
        ColumnSpec("stream", ("stream",)),
        ColumnSpec("graduation_year", ("current year of graduation",)),
        ColumnSpec("skills", ("other skills",)),
        ColumnSpec(
            "availability",
            (
                "please confirm your availability for this internship if not "
                "available immediately how early would you be able to join",
            ),
        ),
        ColumnSpec("resume_link", ("uploaded resume cv link",)),
    ),
    notes=(
        "Export from the Internshala employer dashboard (Premium): "
        "Applications → Export to Excel. The application link column is used "
        "to recognise the same applicant across re-exports."
    ),
)
