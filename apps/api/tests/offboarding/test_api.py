"""
The exit workflow over HTTP.

The point of this file is the gates as a client experiences them: what a
403 looks like, what the blocker list contains, and that no route lets an
employee walk themselves out.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"
RESIGNATIONS = "/api/v1/resignations/"
EXITS = "/api/v1/exits/"
CLEARANCE = "/api/v1/exit-clearance-items/"
EMPLOYEES = "/api/v1/employees/"

EXCEPTION_REASON = "Replacement has started early and the handover is already complete."


def _auth(api, user):
    token = api.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


# ===================================================== resignation


def test_an_employee_resigns_over_http(api, leaver, last_working_date):
    _auth(api, leaver.user)

    response = api.post(
        RESIGNATIONS,
        {
            "requested_last_working_date": str(last_working_date),
            "reason": "better_opportunity",
            "comments": "Thank you for the opportunity.",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["status"] == "submitted"

    # Still employed — a resignation is a request.
    leaver.refresh_from_db()
    assert leaver.status == "confirmed"


def test_an_employee_cannot_approve_their_own_resignation_over_http(
    api, leaver, last_working_date
):
    _auth(api, leaver.user)
    created = api.post(
        RESIGNATIONS,
        {"requested_last_working_date": str(last_working_date), "reason": "personal"},
        format="json",
    )
    response = api.post(f"{RESIGNATIONS}{created.data['id']}/approve/", {}, format="json")
    assert response.status_code == 403


def test_an_employee_cannot_set_their_status_to_resigned_over_http(api, leaver):
    """The status route is closed to self-service, so the request path is the only way."""
    _auth(api, leaver.user)
    response = api.post(
        f"{EMPLOYEES}{leaver.pk}/status/",
        {"status": "resigned", "reason": "Resigning myself directly."},
        format="json",
    )
    assert response.status_code == 403


def test_hr_approves_and_the_exit_opens(api, leaver, staff, last_working_date, exit_config):
    _auth(api, leaver.user)
    created = api.post(
        RESIGNATIONS,
        {"requested_last_working_date": str(last_working_date), "reason": "relocation"},
        format="json",
    )

    _auth(api, staff["hr_head"].user)
    approved = api.post(
        f"{RESIGNATIONS}{created.data['id']}/approve/",
        {"notes": "Accepted."},
        format="json",
    )
    assert approved.status_code == 201, approved.data
    assert approved.data["stage"] == "notice_period"
    assert len(approved.data["clearance_items"]) > 0
    assert approved.data["blockers"], "a fresh exit must have outstanding gates"

    leaver.refresh_from_db()
    assert leaver.status == "resigned"


def test_the_hr_queue_lists_only_open_resignations(api, leaver, staff, last_working_date):
    _auth(api, leaver.user)
    api.post(
        RESIGNATIONS,
        {"requested_last_working_date": str(last_working_date), "reason": "personal"},
        format="json",
    )

    _auth(api, staff["hr_head"].user)
    queue = api.get(f"{RESIGNATIONS}pending/")
    assert queue.status_code == 200
    assert all(row["status"] == "submitted" for row in queue.data["data"])


# ===================================================== the blocker list


def test_the_detail_view_names_every_open_gate(api, exit_workflow, staff, laptop):
    _auth(api, staff["hr_head"].user)
    response = api.get(f"{EXITS}{exit_workflow.pk}/")

    assert response.status_code == 200
    assert response.data["can_complete"] is False

    gates = {blocker["gate"] for blocker in response.data["blockers"]}
    assert "assets" in gates
    assert "finance" in gates
    assert {"hr", "department", "it"} <= gates

    # The unreturned asset is named, so the user knows what to chase.
    assert response.data["unreturned_assets"][0]["asset_tag"] == laptop.asset_tag


def test_approval_is_refused_while_a_gate_is_open(api, exit_workflow, staff, laptop):
    _auth(api, staff["hr_head"].user)
    response = api.post(f"{EXITS}{exit_workflow.pk}/approve/", {}, format="json")

    assert response.status_code == 400
    assert "blockers" in str(response.data)


def test_the_full_exit_over_http(
    api, exit_workflow, staff, laptop, clear_everything, leaver
):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    _auth(api, staff["hr_head"].user)

    detail = api.get(f"{EXITS}{exit_workflow.pk}/")
    assert detail.data["blockers"] == []
    assert detail.data["can_complete"] is True

    approved = api.post(
        f"{EXITS}{exit_workflow.pk}/approve/", {"notes": "Cleared."}, format="json"
    )
    assert approved.status_code == 200
    assert approved.data["stage"] == "approved"

    completed = api.post(f"{EXITS}{exit_workflow.pk}/complete/", {}, format="json")
    assert completed.status_code == 200
    assert completed.data["stage"] == "completed"

    leaver.refresh_from_db()
    assert leaver.status == "exited"


def test_an_exited_employee_offers_no_further_transitions(
    api, exit_workflow, staff, laptop, clear_everything, leaver
):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    _auth(api, staff["hr_head"].user)
    api.post(f"{EXITS}{exit_workflow.pk}/approve/", {}, format="json")
    api.post(f"{EXITS}{exit_workflow.pk}/complete/", {}, format="json")

    profile = api.get(f"{EMPLOYEES}{leaver.pk}/profile/")
    assert profile.data["employee"]["status"] == "exited"
    # EXITED is terminal, so the picker has nothing to offer.
    assert profile.data["allowed_status_transitions"] == []

    reactivate = api.post(
        f"{EMPLOYEES}{leaver.pk}/status/",
        {"status": "active", "reason": "Attempting to bring them back."},
        format="json",
    )
    assert reactivate.status_code == 400


# ===================================================== notice exceptions


def test_a_manager_cannot_waive_notice_over_http(api, exit_workflow, staff):
    _auth(api, staff["medical_director"].user)
    response = api.post(
        f"{EXITS}{exit_workflow.pk}/waive-notice/",
        {"reason": EXCEPTION_REASON},
        format="json",
    )
    assert response.status_code == 403


def test_waiving_notice_needs_a_substantial_reason_at_the_api(api, exit_workflow, staff):
    _auth(api, staff["hr_head"].user)
    refused = api.post(
        f"{EXITS}{exit_workflow.pk}/waive-notice/", {"reason": "no"}, format="json"
    )
    assert refused.status_code == 400

    allowed = api.post(
        f"{EXITS}{exit_workflow.pk}/waive-notice/",
        {"reason": EXCEPTION_REASON},
        format="json",
    )
    assert allowed.status_code == 200
    assert allowed.data["notice_waived"] is True


def test_early_release_requires_a_new_date(api, exit_workflow, staff):
    _auth(api, staff["hr_head"].user)
    missing = api.post(
        f"{EXITS}{exit_workflow.pk}/early-release/",
        {"reason": EXCEPTION_REASON},
        format="json",
    )
    assert missing.status_code == 400

    earlier = exit_workflow.expected_last_working_date - dt.timedelta(days=10)
    ok = api.post(
        f"{EXITS}{exit_workflow.pk}/early-release/",
        {"reason": EXCEPTION_REASON, "new_last_working_date": str(earlier)},
        format="json",
    )
    assert ok.status_code == 200
    assert ok.data["early_release_approved"] is True


# ===================================================== clearance over HTTP


def test_the_owning_role_completes_its_item_and_others_cannot(api, exit_workflow, staff):
    hr_item = exit_workflow.clearance_items.filter(category="hr", owner="hr").first()

    _auth(api, staff["finance_head"].user)
    refused = api.post(f"{CLEARANCE}{hr_item.pk}/complete/", {}, format="json")
    assert refused.status_code == 403

    _auth(api, staff["hr_head"].user)
    allowed = api.post(f"{CLEARANCE}{hr_item.pk}/complete/", {}, format="json")
    assert allowed.status_code == 200
    assert allowed.data["status"] == "completed"


def test_my_clearance_queue_shows_only_my_items(api, exit_workflow, staff, leaver):
    _auth(api, staff["medical_director"].user)
    response = api.get(f"{CLEARANCE}mine/")

    assert response.status_code == 200
    assert len(response.data) > 0
    for row in response.data:
        assert str(row["assigned_to"]) == str(staff["medical_director"].pk)


def test_an_employee_sees_their_own_clearance_items_only(api, exit_workflow, leaver, staff):
    _auth(api, leaver.user)
    response = api.get(CLEARANCE, {"exit_workflow": str(exit_workflow.pk)})

    assert response.status_code == 200
    # Self scope on OFFBOARDING resolves to their own exit's items.
    assert all(
        str(row["exit_workflow"]) == str(exit_workflow.pk) for row in response.data["data"]
    )


# ===================================================== settlement over HTTP


def test_finance_prepares_and_clears_over_http(api, exit_workflow, staff):
    _auth(api, staff["accounts_manager"].user)
    prepared = api.post(
        f"{EXITS}{exit_workflow.pk}/settlement/",
        {"pending_salary": "40000.00", "leave_encashment": "8000.00"},
        format="json",
    )
    assert prepared.status_code == 200
    assert prepared.data["net_payable"] == "48000.00"

    # Preparing is not clearing.
    refused = api.post(f"{EXITS}{exit_workflow.pk}/clear-settlement/", {}, format="json")
    assert refused.status_code == 403

    _auth(api, staff["finance_head"].user)
    cleared = api.post(f"{EXITS}{exit_workflow.pk}/clear-settlement/", {}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["status"] == "cleared"


# ===================================================== interview and scope


def test_hr_records_the_exit_interview_over_http(api, exit_workflow, staff):
    _auth(api, staff["hr_head"].user)
    response = api.post(
        f"{EXITS}{exit_workflow.pk}/interview/",
        {
            "primary_reason": "compensation",
            "employee_feedback": "Good team, pay below market.",
            "rehire_eligibility": "eligible",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["is_conducted"] is True
    assert response.data["rehire_eligibility"] == "eligible"


def test_a_department_head_sees_only_their_own_departments_exits(
    api, exit_workflow, staff
):
    _auth(api, staff["operational_head"].user)
    # The leaver is in the medical department.
    assert api.get(f"{EXITS}{exit_workflow.pk}/").status_code == 404

    _auth(api, staff["medical_director"].user)
    assert api.get(f"{EXITS}{exit_workflow.pk}/").status_code == 200


def test_an_employee_sees_their_own_exit_but_cannot_approve_it(api, exit_workflow, leaver):
    _auth(api, leaver.user)

    visible = api.get(f"{EXITS}{exit_workflow.pk}/")
    assert visible.status_code == 200
    assert visible.data["employee_code"] == leaver.employee_code

    refused = api.post(f"{EXITS}{exit_workflow.pk}/approve/", {}, format="json")
    assert refused.status_code == 403
