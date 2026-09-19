"""
The candidate import API: routing, authorization, throttling and disclosure.

The route gate is the first of three checks — the service re-authorises and
re-resolves the job independently, because a service must be safe when reached
from a management command or a Celery task too.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

BASE = "/api/v1/candidate-imports/"


def _auth(api, user):
    api.force_authenticate(user=user)
    return api


# ------------------------------------------------------------- platforms


def test_the_platform_list_names_what_cannot_be_supported(api, importer_user):
    """
    Unavailable platforms are RETURNED, with the reason.

    Omitting them would invite the assumption that support is merely pending.
    This says what is actually missing and what would unblock it.
    """
    response = _auth(api, importer_user).get(f"{BASE}platforms/")

    assert response.status_code == 200
    by_key = {p["key"]: p for p in response.data}

    assert by_key["workindia"]["available"] is True
    assert by_key["naukri"]["available"] is True
    assert by_key["internshala"]["available"] is True
    for key in ("indeed", "linkedin"):
        assert by_key[key]["available"] is False
        assert by_key[key]["unavailable_reason"]

    assert "not currently accepting" in by_key["linkedin"]["unavailable_reason"].lower()


# ----------------------------------------------------------------- RBAC


def test_a_role_without_import_cannot_reach_the_endpoint(api, make_user, import_job, workindia_xlsx):
    response = _auth(api, make_user("employee")).post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    assert response.status_code == 403


def test_a_recruiter_can_upload_and_preview(api, importer_user, import_job, workindia_xlsx):
    response = _auth(api, importer_user).post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["rows_total"] == 2
    assert response.data["status"] == "parsed"


def test_uploading_writes_no_candidates(api, importer_user, import_job, workindia_xlsx):
    """Preview is preview. Nothing reaches the pipeline until commit."""
    from apps.recruitment.models import Candidate

    before = Candidate.objects.count()
    _auth(api, importer_user).post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    assert Candidate.objects.count() == before


def test_an_unavailable_platform_is_refused_over_http(
    api, importer_user, import_job, workindia_xlsx
):
    response = _auth(api, importer_user).post(
        BASE,
        {"platform": "linkedin", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    assert response.status_code == 422


def test_a_job_outside_the_callers_scope_is_a_404_not_a_403(
    api, user_for, ops_job, workindia_xlsx, everyone_grant
):
    """
    Resource concealment. A 403 would confirm the job exists, which is an
    enumeration oracle over another department's hiring.
    """
    director = user_for("medical_director")
    everyone_grant(director, "candidate", "import", 3)  # DEPARTMENT

    response = _auth(api, director).post(
        BASE,
        {"platform": "workindia", "job_opening": str(ops_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    assert response.status_code == 404


# ------------------------------------------------------ attest and commit


def test_commit_requires_an_attestation(api, importer_user, import_job, workindia_xlsx):
    client = _auth(api, importer_user)
    created = client.post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    response = client.post(f"{BASE}{created.data['id']}/commit/")

    assert response.status_code == 422


def test_a_short_attestation_note_is_refused(api, importer_user, import_job, workindia_xlsx):
    """Same 20-character floor as a rejection rationale, and for the same reason."""
    client = _auth(api, importer_user)
    created = client.post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )

    response = client.post(
        f"{BASE}{created.data['id']}/attest/",
        {
            "legal_basis": "voluntarily_provided",
            "legal_basis_note": "because",
            "attestation_text": "I confirm.",
        },
    )

    assert response.status_code == 400


def test_the_full_upload_attest_commit_flow(api, importer_user, import_job, workindia_xlsx):
    from apps.recruitment.models import Application

    client = _auth(api, importer_user)
    created = client.post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )
    batch_id = created.data["id"]

    attested = client.post(
        f"{BASE}{batch_id}/attest/",
        {
            "legal_basis": "voluntarily_provided",
            "legal_basis_note": "Sourced from our own employer dashboard export.",
            "attestation_text": "I confirm a lawful basis exists for this import.",
        },
    )
    assert attested.status_code == 200

    committed = client.post(f"{BASE}{batch_id}/commit/")

    assert committed.status_code == 200
    assert committed.data["created"] == 2
    assert Application.objects.filter(job_opening=import_job).count() == 2


# ------------------------------------------------------------ disclosure


def test_a_peer_cannot_read_another_users_batch(
    api, importer_user, import_job, workindia_xlsx, user_for
):
    """
    A preview payload is a raw dump of somebody's spreadsheet. Below
    organisation-wide scope, batches are visible only to the uploader.
    """
    created = _auth(api, importer_user).post(
        BASE,
        {"platform": "workindia", "job_opening": str(import_job.pk), "file": workindia_xlsx()},
        format="multipart",
    )
    batch_id = created.data["id"]

    # hr_manager holds IMPORT at ALL, so use a DEPARTMENT-scoped principal.
    other = user_for("medical_director")
    api.force_authenticate(user=None)
    response = _auth(api, other).get(f"{BASE}{batch_id}/")

    assert response.status_code in (403, 404)


def test_the_error_report_is_json_and_carries_no_cell_values(
    api, importer_user, import_job, workindia_xlsx
):
    """
    JSON, not CSV: a CSV of candidate-derived values is a formula-injection
    vector against whoever opens it.
    """
    client = _auth(api, importer_user)
    created = client.post(
        BASE,
        {
            "platform": "workindia",
            "job_opening": str(import_job.pk),
            "file": workindia_xlsx(rows=[
                ["WI-9", "", "9876543299", "", "", "", "", "", "", "", ""],
            ]),
        },
        format="multipart",
    )

    response = client.get(f"{BASE}{created.data['id']}/errors/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    row = response.data["rows"][0]
    assert row["errors"][0]["code"] == "missing_name"
    # Codes and row numbers only — no cell content in the error object.
    assert "value" not in row["errors"][0]


def test_the_endpoint_declares_a_throttle_scope():
    """
    ScopedRateThrottle silently allows any view missing `throttle_scope`, so an
    omission here would be invisible rather than loud.
    """
    from apps.imports.api import CandidateImportViewSet

    assert CandidateImportViewSet.throttle_scope == "candidate_import"
    # And only the expensive halves spend it. Throttling the reads burned
    # the hourly budget on page loads: opening the wizard hit /platforms/,
    # every staged-row edit refetched the batch, and by mid-session HR was
    # locked out of step 1 with "expected available in 2104 seconds".
    assert CandidateImportViewSet.THROTTLED_ACTIONS == frozenset({"create", "commit"})

    view = CandidateImportViewSet()
    view.action = "platforms"
    assert view.get_throttles() == []
    view.action = "create"
    assert view.get_throttles(), "the upload must stay rate-limited"


def test_every_custom_route_names_its_action():
    """
    `resolve_action` falls through to METHOD_ACTION_MAP for an unmapped custom
    route, so POST /commit/ would be authorised as Action.CREATE — which several
    roles hold and which is not the authority this endpoint requires.
    """
    from apps.imports.api import CandidateImportViewSet
    from core.access import Action

    mapped = CandidateImportViewSet.access_actions
    for route in ("create", "commit", "attest", "destroy"):
        assert mapped[route] == Action.IMPORT, f"{route} is not gated on IMPORT"


# ------------------------------------------ Action.IMPORT blast radius


def test_adding_import_granted_nothing_else():
    """
    The whole matrix, re-derived. Adding an action to a shared resource is
    exactly where an unintended grant hides, so this asserts the complete set
    rather than spot-checking the four roles we meant to change.
    """
    from apps.accounts.permission_matrix import ROLE_SPECS
    from core.access.catalog import Action, Resource

    holders = {
        spec.code
        for spec in ROLE_SPECS
        if Action.IMPORT in spec.permissions.get(Resource.CANDIDATE, {})
    }
    assert holders == {"admin", "hr_head", "hr_manager", "recruiter"}

    # And IMPORT appears on exactly two other resources, both deliberate:
    #
    #   * ATTENDANCE_DEVICE, the eSSL sync trigger, which is the HR Head's
    #     alone (Admin's comes from RolePermission seeding of ADMIN_MANAGED,
    #     not from an explicit matrix cell);
    #   * EMPLOYEE, bulk hire from a staff list, held by the three roles that
    #     already hire one at a time. Separately grantable from EMPLOYEE/CREATE
    #     for the same reason CANDIDATE/IMPORT is separate from CANDIDATE/
    #     CREATE: hiring one person and hiring two hundred in a single action
    #     are different amounts of trust.
    #
    # The set is asserted whole, not appended to, because the point of this
    # test is that a new IMPORT cell has to be argued for HERE before it ships.
    elsewhere = {
        (spec.code, resource)
        for spec in ROLE_SPECS
        for resource, actions in spec.permissions.items()
        if Action.IMPORT in actions and resource != Resource.CANDIDATE
    }
    assert elsewhere == {
        ("hr_head", Resource.ATTENDANCE_DEVICE),
        ("admin", Resource.EMPLOYEE),
        ("hr_head", Resource.EMPLOYEE),
        ("hr_manager", Resource.EMPLOYEE),
    }, f"IMPORT leaked onto {sorted(elsewhere)}"

    # Nobody below those three, and nothing that only VIEWS people: a recruiter
    # bulk-ingests candidates and does not hire.
    employee_importers = {
        spec.code
        for spec in ROLE_SPECS
        if Action.IMPORT in spec.permissions.get(Resource.EMPLOYEE, {})
    }
    assert "recruiter" not in employee_importers
    assert "employee" not in employee_importers


def test_the_ceo_cannot_import_however_the_matrix_is_edited(everyone_grant, user_for):
    """
    The read-only clamp runs last and unconditionally. Even an explicit
    override cannot give a read-only principal a write.
    """
    from core.access import Action, Resource, Scope, can

    ceo = user_for("ceo")
    everyone_grant(ceo, Resource.CANDIDATE, Action.IMPORT, Scope.ALL)

    assert can(ceo, Resource.CANDIDATE, Action.IMPORT) == Scope.NONE


def test_no_candidate_pii_reaches_the_logs(
    importer_user, import_job, workindia_xlsx, caplog
):
    """
    The log line is counts, ids and a hash prefix. Container logs are not a PII
    store, and this is the path that would quietly make them one.
    """
    import logging

    from apps.imports.platforms import WORKINDIA
    from apps.imports.services import importer

    with caplog.at_level(logging.INFO, logger="hrms.audit"):
        importer.create_batch(
            actor=importer_user, platform=WORKINDIA,
            job_opening=import_job, file=workindia_xlsx(),
        )

    text = "\n".join(record.getMessage() for record in caplog.records)
    for pii in ("Priya", "Deshmukh", "priya@example.test", "9876543210", "Ramesh"):
        assert pii not in text, f"{pii} reached the application log"


def test_sentry_is_configured_not_to_ship_local_variables():
    """
    `send_default_pii=False` covers request bodies and user identity. It does
    NOT stop stack-frame locals being captured — and a frame inside the row loop
    holds the row. Without this, an unhandled import error ships a candidate's
    name, address and phone to a third party.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "config" / "settings" / "prod.py"
    ).read_text(encoding="utf-8")

    assert "include_local_variables=False" in source, (
        "Sentry would capture stack-frame locals, which in the import row loop "
        "means candidate PII."
    )
