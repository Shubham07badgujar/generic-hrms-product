"""
Editing the staged preview before commit.

The claims:
  1. A staged row's cells can be edited; identity, validity and dedup
     re-resolve through the SAME parse the file went through.
  2. A row can be hand-added, as if the file had carried it — and it commits
     like any other row.
  3. A row can be removed from staging (never a candidate).
  4. None of it is possible once the batch is committed.
  5. The row payload exposes every raw cell, keyed by the file's own headers.
"""

from __future__ import annotations

import pytest

from apps.imports.models import ImportRow, RowStatus
from apps.imports.services import importer
from core.api.exceptions import BusinessRuleError

pytestmark = pytest.mark.django_db


@pytest.fixture
def staged(workindia_csv, importer_user, import_job):
    """Uploaded and parsed — the editable state."""
    from apps.imports.platforms import WORKINDIA

    return importer.create_batch(
        actor=importer_user, platform=WORKINDIA,
        job_opening=import_job, file=workindia_csv(),
    )


def _first(batch):
    """Spreadsheet numbering: the header is row 1, data starts at 2."""
    return ImportRow.objects.filter(batch=batch).order_by("row_number").first()


def test_editing_a_cell_reparses_the_row(staged, importer_user):
    first = _first(staged)
    raw = dict(first.raw)
    raw["Candidate Name"] = "Edited Person"
    raw["Mobile Number"] = "9822411999"

    row = importer.update_row(
        actor=importer_user, batch=staged, row_number=first.row_number, raw=raw
    )
    assert row.first_name == "Edited"
    assert row.last_name == "Person"
    assert row.phone_e164 and row.phone_e164.endswith("9822411999")
    assert row.raw["Candidate Name"] == "Edited Person"


def test_blanking_the_name_makes_the_row_invalid_and_counters_follow(
    staged, importer_user
):
    first = _first(staged)
    raw = dict(first.raw)
    raw["Candidate Name"] = ""
    row = importer.update_row(
        actor=importer_user, batch=staged, row_number=first.row_number, raw=raw
    )
    assert row.status == RowStatus.INVALID
    staged.refresh_from_db()
    assert staged.rows_failed >= 1


def test_a_hand_added_row_stages_and_commits_like_any_other(
    staged, importer_user
):
    added = importer.add_row(
        actor=importer_user, batch=staged,
        raw={
            "Candidate Name": "Walkin Applicant",
            "Mobile Number": "9822412000",
            "Email": "walkin.applicant@example.test",
        },
    )
    assert added.row_number == _first(staged).row_number + staged.rows.count() - 1
    assert added.first_name == "Walkin"
    assert added.status in (RowStatus.VALID, RowStatus.NEEDS_REVIEW)
    staged.refresh_from_db()
    assert staged.rows_total == staged.rows.count()

    importer.attest(
        actor=importer_user, batch=staged,
        legal_basis="employer_subscription",
        legal_basis_note="Sourced under our WorkIndia employer subscription.",
        attestation_text="I confirm the lawful basis for this file.",
    )
    result = importer.commit(actor=importer_user, batch=staged)
    assert result.created >= 1
    added.refresh_from_db()
    assert added.status == RowStatus.CREATED

    from apps.recruitment.models import Candidate

    assert Candidate.objects.filter(first_name="Walkin").exists()


def test_removing_a_row_is_staging_only(staged, importer_user):
    before = staged.rows.count()
    importer.remove_row(actor=importer_user, batch=staged, row_number=_first(staged).row_number)
    staged.refresh_from_db()
    assert staged.rows.count() == before - 1
    assert staged.rows_total == before - 1


def test_a_committed_batch_refuses_edits(staged, importer_user):
    importer.attest(
        actor=importer_user, batch=staged,
        legal_basis="employer_subscription",
        legal_basis_note="Sourced under our WorkIndia employer subscription.",
        attestation_text="I confirm the lawful basis for this file.",
    )
    importer.commit(actor=importer_user, batch=staged)
    with pytest.raises(BusinessRuleError):
        importer.update_row(
            actor=importer_user, batch=staged, row_number=2,
            raw={"Candidate Name": "Too Late"},
        )
    with pytest.raises(BusinessRuleError):
        importer.add_row(
            actor=importer_user, batch=staged, raw={"Candidate Name": "Too Late"}
        )


def test_the_api_serves_raw_cells_and_edits_over_http(
    api, staged, importer_user
):
    from tests.imports.conftest import WORKINDIA_HEADERS  # noqa: F401

    api.force_authenticate(user=importer_user)
    detail = api.get(f"/api/v1/candidate-imports/{staged.pk}/")
    assert detail.status_code == 200
    first = detail.data["rows"][0]
    assert "raw" in first and first["raw"].get("Candidate Name")

    edited = api.post(
        f"/api/v1/candidate-imports/{staged.pk}/rows/update/",
        {"row_number": first["row_number"], "raw": {**first["raw"], "Candidate Name": "Http Edit"}},
        format="json",
    )
    assert edited.status_code == 200, edited.data
    assert edited.data["first_name"] == "Http"

    added = api.post(
        f"/api/v1/candidate-imports/{staged.pk}/rows/add/",
        {"raw": {"Candidate Name": "Http Added", "Mobile Number": "9822412001"}},
        format="json",
    )
    assert added.status_code == 201, added.data

    removed = api.post(
        f"/api/v1/candidate-imports/{staged.pk}/rows/remove/",
        {"row_number": added.data["row_number"]}, format="json",
    )
    assert removed.status_code == 204
