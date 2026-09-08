"""
The two-step approval chain: Employee -> Reporting Manager -> HR Head.

The claims: a request starts with the CONFIGURED reporting manager; the
manager's approval only FORWARDS it (nothing granted, no balance moved); the
HR Head's approval is the one that makes leave real; a manager's rejection
ends the process; and every guard — wrong manager, self-decision, stage
jumping, no manager at all — refuses correctly.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.leave import services
from apps.leave.models import LeaveRequest, LeaveStatus
from apps.leave.services import LeaveError

# Reuse the module's staff/leave_config fixtures and _apply helper.
from tests.leave.test_leave_module import PASSWORD, _apply, leave_config, staff  # noqa: F401

pytestmark = pytest.mark.django_db


def test_a_request_starts_with_the_reporting_manager(staff, leave_config):
    request = _apply(staff, leave_config)  # therapist reports to medical_director
    assert request.approval_stage == "manager"
    assert request.status == LeaveStatus.PENDING


def test_the_manager_approval_forwards_but_grants_nothing(staff, leave_config):
    request = _apply(staff, leave_config)
    balance = services.balance_for(
        staff["therapist"], request.leave_type, request.start_date.year
    )
    held = balance.pending

    forwarded = services.approve_leave(
        request=request, actor=staff["medical_director"].user, note="Fine by me."
    )

    assert forwarded.status == LeaveStatus.PENDING          # NOT approved yet
    assert forwarded.approval_stage == "hr"
    assert forwarded.manager_decided_by == staff["medical_director"].user
    assert forwarded.manager_note == "Fine by me."
    balance.refresh_from_db()
    assert balance.pending == held                           # hold unchanged
    assert balance.used == 0


def test_the_hr_head_approval_is_the_one_that_grants(staff, leave_config):
    request = _apply(staff, leave_config)
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()

    done = services.approve_leave(request=request, actor=staff["hr_head"].user)

    assert done.status == LeaveStatus.APPROVED
    assert done.decided_by == staff["hr_head"].user
    balance = services.balance_for(
        staff["therapist"], request.leave_type, request.start_date.year
    )
    assert balance.used == request.days
    assert balance.pending == 0


def test_the_hr_head_cannot_jump_the_manager_stage(staff, leave_config):
    request = _apply(staff, leave_config)
    with pytest.raises(LeaveError, match="reporting manager"):
        services.approve_leave(request=request, actor=staff["hr_head"].user)


def test_only_the_configured_manager_acts_at_the_manager_stage(staff, leave_config):
    """Another manager — even a Head — is not this employee's manager."""
    request = _apply(staff, leave_config)
    with pytest.raises(LeaveError, match="reporting manager"):
        services.approve_leave(request=request, actor=staff["hr_manager"].user)


def test_a_manager_rejection_ends_the_request(staff, leave_config):
    request = _apply(staff, leave_config)
    done = services.reject_leave(
        request=request, actor=staff["medical_director"].user,
        reason="Clinic is short-staffed that week.",
    )
    assert done.status == LeaveStatus.REJECTED
    assert done.manager_decided_by == staff["medical_director"].user
    # The pending hold was released.
    balance = services.balance_for(
        staff["therapist"], request.leave_type, request.start_date.year
    )
    assert balance.pending == 0

    # And HR cannot resurrect it — it never reaches them.
    with pytest.raises(LeaveError, match="already"):
        services.approve_leave(request=request, actor=staff["hr_head"].user)


def test_an_employee_without_a_manager_goes_straight_to_hr(staff, leave_config):
    """medical_director has no reporting manager configured."""
    request = _apply(staff, leave_config, who="medical_director", days_from_now=10)
    assert request.approval_stage == "hr"

    done = services.approve_leave(request=request, actor=staff["hr_head"].user)
    assert done.status == LeaveStatus.APPROVED


def test_a_manager_who_cannot_approve_is_routed_past(staff, leave_config):
    """A configured manager whose roles carry no approval right would strand
    the request — it goes straight to the HR Head instead."""
    boss = staff["medical_director"]
    boss.reporting_manager = staff["therapist"]  # therapist holds no APPROVE
    boss.save(update_fields=["reporting_manager"])

    request = _apply(staff, leave_config, who="medical_director", days_from_now=20)
    assert request.approval_stage == "hr"


def test_a_manager_who_is_the_hr_head_collapses_to_one_step(staff, leave_config):
    """hr_manager reports to the HR Head: no double approval by one person."""
    request = _apply(staff, leave_config, who="hr_manager", days_from_now=12)
    assert request.approval_stage == "hr"


def test_the_manager_cannot_take_the_final_decision(staff, leave_config):
    request = _apply(staff, leave_config)
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()

    with pytest.raises(LeaveError, match="HR Head"):
        services.approve_leave(request=request, actor=staff["medical_director"].user)


def test_can_decide_marks_exactly_whose_turn_it_is(api, staff, leave_config):
    """The UI's button flag: true only for the current stage's decider."""
    request = _apply(staff, leave_config)  # therapist -> MD -> HR Head

    def login(role):
        token = api.post(
            "/api/v1/auth/login/",
            {"email": f"{role}@leave.test", "password": PASSWORD},
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def flag():
        rows = api.get("/api/v1/leave-requests/", {"status": "pending"}).data["data"]
        return next(r["can_decide"] for r in rows if r["id"] == str(request.pk))

    login("hr_head")
    assert flag() is False          # manager stage: not HR's turn yet
    login("medical_director")
    assert flag() is True           # the configured manager's turn

    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()

    login("medical_director")
    assert flag() is False          # forwarded: manager is done
    login("hr_head")
    assert flag() is True           # now it IS HR's turn


def test_nobody_decides_their_own_request_at_any_stage(staff, leave_config):
    """Even an approver-role holder cannot decide their own request."""
    request = _apply(staff, leave_config, who="medical_director", days_from_now=15)
    with pytest.raises(LeaveError, match="own leave"):
        services.approve_leave(request=request, actor=staff["medical_director"].user)
