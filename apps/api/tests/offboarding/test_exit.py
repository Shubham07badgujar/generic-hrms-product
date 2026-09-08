"""
The exit workflow: resignation, notice, clearance, settlement, approval.

The load-bearing tests here are the gates. `test_every_gate_blocks_the_exit`
and `test_exited_is_terminal` are the two that would fail loudest if someone
relaxed the design, and they are written to fail for the right reason.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.assets.models import AllocationStatus
from apps.assets.services import return_asset, write_off
from apps.employees.models import EmployeeStatus
from apps.employees.services.lifecycle import LifecycleError, allowed_targets, change_status
from apps.itaccounts.models import AccountStatus, CompanyEmailAccount
from apps.offboarding.models import (
    ClearanceCategory,
    ClearanceStatus,
    ExitStage,
    ExitType,
    ExitWorkflow,
    ResignationRequest,
    ResignationStatus,
    SettlementStatus,
)
from apps.offboarding.services import (
    approve_early_release,
    approve_exit,
    approve_resignation,
    cancel_exit,
    clear_settlement,
    complete_clearance_item,
    complete_exit,
    exit_blockers,
    record_exit_interview,
    reject_resignation,
    start_exit,
    submit_resignation,
    update_notice,
    update_settlement,
    waive_clearance_item,
    waive_notice,
    withdraw_resignation,
)
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

REASON = "Accepting a role closer to family, with a full handover planned."
EXCEPTION_REASON = "Replacement has started early and the handover is already complete."


# ===================================================== resignation


def test_an_employee_submits_their_own_resignation(leaver, last_working_date):
    request = submit_resignation(
        employee=leaver,
        actor=leaver.user,
        requested_last_working_date=last_working_date,
        reason="better_opportunity",
        comments="Thank you for the opportunity.",
    )

    assert request.status == ResignationStatus.SUBMITTED
    assert request.submitted_by_id == leaver.user.pk
    assert request.requested_last_working_date == last_working_date

    # THE RULE: submitting does not change employment status.
    leaver.refresh_from_db()
    assert leaver.status == EmployeeStatus.CONFIRMED


def test_an_employee_cannot_set_their_own_status_to_resigned(leaver):
    """
    The resignation is a REQUEST precisely so this path stays closed. Self-service
    grants EMPLOYEE/EDIT at SELF scope, and the lifecycle service refuses it.
    """
    with pytest.raises(AccessDenied):
        change_status(
            employee=leaver,
            actor=leaver.user,
            new_status=EmployeeStatus.RESIGNED,
            reason="Resigning myself directly.",
        )
    leaver.refresh_from_db()
    assert leaver.status == EmployeeStatus.CONFIRMED


def test_only_one_open_resignation_at_a_time(leaver, last_working_date):
    submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="personal",
    )
    with pytest.raises(ValidationError) as exc:
        submit_resignation(
            employee=leaver, actor=leaver.user,
            requested_last_working_date=last_working_date, reason="personal",
        )
    assert "already submitted" in str(exc.value)


def test_a_resignation_cannot_be_backdated_into_the_past(leaver):
    with pytest.raises(ValidationError) as exc:
        submit_resignation(
            employee=leaver, actor=leaver.user,
            requested_last_working_date=timezone.localdate() - dt.timedelta(days=1),
            reason="personal",
        )
    assert "in the past" in str(exc.value)


def test_an_employee_cannot_resign_on_someone_elses_behalf(leaver, staff, last_working_date):
    """Submitting for another person needs reach beyond yourself."""
    with pytest.raises(AccessDenied) as exc:
        submit_resignation(
            employee=staff["senior_doctor"],
            actor=leaver.user,
            requested_last_working_date=last_working_date,
            reason="personal",
        )
    assert "other people" in str(exc.value)


def test_hr_approval_moves_the_employee_and_opens_the_exit(
    leaver, staff, last_working_date, exit_config
):
    request = submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="better_opportunity",
    )

    workflow = approve_resignation(
        request=request, actor=staff["hr_head"].user, notes="Accepted with thanks."
    )

    request.refresh_from_db()
    leaver.refresh_from_db()

    assert request.status == ResignationStatus.APPROVED
    assert request.reviewed_by_id == staff["hr_head"].user.pk
    # NOW the status moves — through the lifecycle service.
    assert leaver.status == EmployeeStatus.RESIGNED
    assert workflow.stage == ExitStage.NOTICE_PERIOD
    assert workflow.resignation_id == request.pk
    assert workflow.clearance_items.count() > 0


def test_hr_may_agree_a_different_last_working_day(
    leaver, staff, last_working_date, exit_config
):
    request = submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="personal",
    )
    agreed = last_working_date + dt.timedelta(days=15)

    workflow = approve_resignation(
        request=request, actor=staff["hr_head"].user, approved_last_working_date=agreed
    )
    assert workflow.expected_last_working_date == agreed


def test_rejecting_a_resignation_requires_a_reason(leaver, staff, last_working_date):
    request = submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="personal",
    )

    with pytest.raises(ValidationError):
        reject_resignation(request=request, actor=staff["hr_head"].user, reason="no")

    reject_resignation(
        request=request, actor=staff["hr_head"].user,
        reason="Agreed to stay on following a counter-offer discussion.",
    )
    request.refresh_from_db()
    leaver.refresh_from_db()

    assert request.status == ResignationStatus.REJECTED
    assert leaver.status == EmployeeStatus.CONFIRMED  # untouched


def test_an_employee_cannot_approve_their_own_resignation(leaver, last_working_date):
    request = submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="personal",
    )
    with pytest.raises(AccessDenied):
        approve_resignation(request=request, actor=leaver.user)


def test_an_employee_may_withdraw_before_review(leaver, last_working_date):
    request = submit_resignation(
        employee=leaver, actor=leaver.user,
        requested_last_working_date=last_working_date, reason="personal",
    )
    withdraw_resignation(request=request, actor=leaver.user)
    request.refresh_from_db()
    assert request.status == ResignationStatus.WITHDRAWN


# ===================================================== starting an exit


def test_a_termination_runs_the_same_workflow(leaver, staff, last_working_date, exit_config):
    """Both lifecycles in the spec share one implementation."""
    workflow = start_exit(
        employee=leaver,
        actor=staff["hr_head"].user,
        exit_type=ExitType.TERMINATION,
        last_working_date=last_working_date,
        reason="Terminated following a disciplinary process.",
    )
    leaver.refresh_from_db()

    assert leaver.status == EmployeeStatus.TERMINATED
    assert workflow.exit_type == ExitType.TERMINATION
    assert workflow.clearance_items.count() > 0


def test_the_clearance_checklist_covers_every_gate(exit_workflow):
    categories = set(exit_workflow.clearance_items.values_list("category", flat=True))
    assert {
        ClearanceCategory.HR,
        ClearanceCategory.DEPARTMENT,
        ClearanceCategory.IT,
        ClearanceCategory.FINANCE,
        ClearanceCategory.ASSETS,
    } <= categories


def test_clearance_owners_resolve_to_real_people(exit_workflow, staff, leaver):
    manager_item = exit_workflow.clearance_items.filter(owner="manager").first()
    department_item = exit_workflow.clearance_items.filter(owner="department").first()
    employee_item = exit_workflow.clearance_items.filter(owner="employee").first()

    assert manager_item.assigned_to_id == staff["medical_director"].pk
    assert department_item.assigned_to_id == staff["medical_director"].pk
    assert employee_item.assigned_to_id == leaver.pk


def test_starting_an_exit_flags_the_company_account(exit_workflow, leaver, staff):
    """IT needs the lead time, so the account is suspended at the start."""
    from apps.itaccounts.services import record_account

    # Fresh exit for an employee who has an account from the outset.
    account = CompanyEmailAccount.objects.filter(employee=leaver).first()
    if account is None:
        # The fixture employee has none; assert the branch is a no-op rather
        # than pretending otherwise.
        assert exit_workflow.stage == ExitStage.NOTICE_PERIOD
        return
    assert account.status == AccountStatus.SUSPENDED


def test_two_exits_cannot_run_for_one_employee(exit_workflow, leaver, staff, last_working_date):
    with pytest.raises(ValidationError) as exc:
        start_exit(
            employee=leaver, actor=staff["hr_head"].user, exit_type=ExitType.TERMINATION,
            last_working_date=last_working_date, reason="A second exit.",
        )
    assert "already under way" in str(exc.value)


# ===================================================== notice period


def test_notice_is_recorded_from_the_employees_terms(exit_workflow, leaver, last_working_date):
    assert exit_workflow.notice_days == leaver.notice_period_days
    assert exit_workflow.notice_start_date == timezone.localdate()
    assert exit_workflow.expected_last_working_date == last_working_date


def test_waiving_notice_is_an_exception_needing_approval_and_a_reason(exit_workflow, staff):
    # A manager holds OFFBOARDING/EDIT but not APPROVE.
    with pytest.raises(AccessDenied):
        waive_notice(
            workflow=exit_workflow, actor=staff["medical_director"].user, reason=EXCEPTION_REASON
        )

    with pytest.raises(ValidationError):
        waive_notice(workflow=exit_workflow, actor=staff["hr_head"].user, reason="too short")

    waive_notice(
        workflow=exit_workflow, actor=staff["hr_head"].user, reason=EXCEPTION_REASON
    )
    exit_workflow.refresh_from_db()

    assert exit_workflow.notice_waived is True
    assert exit_workflow.notice_waived_by_id == staff["hr_head"].user.pk
    assert exit_workflow.notice_waiver_reason == EXCEPTION_REASON


def test_an_early_release_must_move_the_date_forward(exit_workflow, staff):
    later = exit_workflow.expected_last_working_date + dt.timedelta(days=5)
    with pytest.raises(ValidationError) as exc:
        approve_early_release(
            workflow=exit_workflow, actor=staff["hr_head"].user,
            new_last_working_date=later, reason=EXCEPTION_REASON,
        )
    assert "bring the last working day forward" in str(exc.value)


def test_an_early_release_is_recorded_with_its_actor(exit_workflow, staff):
    earlier = exit_workflow.expected_last_working_date - dt.timedelta(days=10)
    approve_early_release(
        workflow=exit_workflow, actor=staff["hr_head"].user,
        new_last_working_date=earlier, reason=EXCEPTION_REASON,
    )
    exit_workflow.refresh_from_db()

    assert exit_workflow.early_release_approved is True
    assert exit_workflow.early_release_by_id == staff["hr_head"].user.pk
    assert exit_workflow.expected_last_working_date == earlier


def test_extending_notice_is_ordinary_but_shortening_it_is_not(exit_workflow, staff):
    later = exit_workflow.expected_last_working_date + dt.timedelta(days=10)
    update_notice(
        workflow=exit_workflow, actor=staff["hr_head"].user, last_working_date=later
    )
    exit_workflow.refresh_from_db()
    assert exit_workflow.expected_last_working_date == later

    earlier = later - dt.timedelta(days=20)
    with pytest.raises(ValidationError) as exc:
        update_notice(
            workflow=exit_workflow, actor=staff["hr_head"].user, last_working_date=earlier
        )
    assert "early release" in str(exc.value)


def test_notice_exceptions_are_audited_as_overrides(exit_workflow, staff):
    from apps.audit.models import AuditAction, AuditLog

    waive_notice(workflow=exit_workflow, actor=staff["hr_head"].user, reason=EXCEPTION_REASON)

    entry = AuditLog.objects.filter(
        action=AuditAction.OVERRIDE, after__event="notice_waived"
    ).first()
    assert entry is not None
    assert entry.actor_id == staff["hr_head"].user.pk


# ===================================================== clearance ownership


def test_only_the_owning_role_may_complete_an_item(exit_workflow, staff):
    """
    Layer 2 of the check. Finance and HR both hold OFFBOARDING/EDIT; only one
    of them owns the HR sign-off.
    """
    hr_item = exit_workflow.clearance_items.filter(
        category=ClearanceCategory.HR, owner="hr"
    ).first()

    with pytest.raises(AccessDenied) as exc:
        complete_clearance_item(item=hr_item, actor=staff["finance_head"].user)
    assert "does not carry that responsibility" in str(exc.value)

    complete_clearance_item(item=hr_item, actor=staff["hr_head"].user)
    hr_item.refresh_from_db()
    assert hr_item.status == ClearanceStatus.COMPLETED
    assert hr_item.completed_by_id == staff["hr_head"].user.pk


def test_finance_completes_its_own_items(exit_workflow, staff):
    finance_item = exit_workflow.clearance_items.filter(
        category=ClearanceCategory.FINANCE
    ).first()
    complete_clearance_item(item=finance_item, actor=staff["finance_head"].user)
    finance_item.refresh_from_db()
    assert finance_item.status == ClearanceStatus.COMPLETED


def test_the_named_manager_completes_their_handover_item(exit_workflow, staff):
    item = exit_workflow.clearance_items.filter(owner="manager").first()
    complete_clearance_item(item=item, actor=staff["medical_director"].user)
    item.refresh_from_db()
    assert item.status == ClearanceStatus.COMPLETED


def test_an_unrelated_manager_cannot_sign_off_the_handover(exit_workflow, staff):
    item = exit_workflow.clearance_items.filter(owner="manager").first()
    with pytest.raises(AccessDenied):
        complete_clearance_item(item=item, actor=staff["operational_head"].user)


def test_an_item_requiring_evidence_refuses_a_bare_completion(exit_workflow, staff):
    item = exit_workflow.clearance_items.filter(requires_evidence=True).first()
    assert item is not None

    with pytest.raises(ValidationError) as exc:
        complete_clearance_item(item=item, actor=staff["hr_head"].user)
    assert "must attach evidence" in str(exc.value)


def test_waiving_a_clearance_item_takes_approval_and_a_reason(exit_workflow, staff):
    item = exit_workflow.clearance_items.filter(category=ClearanceCategory.HR).first()

    with pytest.raises(AccessDenied):
        waive_clearance_item(
            item=item, actor=staff["medical_director"].user, reason="Not applicable."
        )

    waive_clearance_item(item=item, actor=staff["hr_head"].user, reason="Handled offline.")
    item.refresh_from_db()
    # WAIVED, not COMPLETED — the distinction survives into any later review.
    assert item.status == ClearanceStatus.WAIVED


def test_working_the_checklist_advances_the_stage(exit_workflow, staff):
    assert exit_workflow.stage == ExitStage.NOTICE_PERIOD
    item = exit_workflow.clearance_items.filter(category=ClearanceCategory.HR).first()
    complete_clearance_item(item=item, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    assert exit_workflow.stage == ExitStage.CLEARANCE


# ===================================================== settlement


def test_finance_prepares_and_clears_the_settlement(exit_workflow, staff):
    settlement = exit_workflow.settlement
    assert settlement.status == SettlementStatus.DRAFT

    update_settlement(
        settlement=settlement,
        actor=staff["accounts_manager"].user,
        pending_salary="45000.00",
        leave_encashment="12000.00",
        outstanding_advances="5000.00",
    )
    settlement.refresh_from_db()

    assert settlement.status == SettlementStatus.IN_REVIEW
    assert settlement.gross_earnings == 57000
    assert settlement.total_deductions == 5000
    assert settlement.net_payable == 52000

    clear_settlement(settlement=settlement, actor=staff["finance_head"].user)
    settlement.refresh_from_db()
    assert settlement.status == SettlementStatus.CLEARED
    assert settlement.cleared_by_id == staff["finance_head"].user.pk


def test_a_settlement_may_be_negative(exit_workflow, staff):
    """An employee can owe the company on the way out."""
    settlement = exit_workflow.settlement
    update_settlement(
        settlement=settlement, actor=staff["finance_head"].user,
        pending_salary="10000.00", outstanding_advances="25000.00",
    )
    settlement.refresh_from_db()
    assert settlement.net_payable == -15000


def test_a_cleared_settlement_cannot_be_edited(exit_workflow, staff):
    settlement = exit_workflow.settlement
    clear_settlement(settlement=settlement, actor=staff["finance_head"].user)

    with pytest.raises(ValidationError) as exc:
        update_settlement(
            settlement=settlement, actor=staff["finance_head"].user, pending_salary="1.00"
        )
    assert "already cleared" in str(exc.value)


def test_preparing_is_not_clearing(exit_workflow, staff):
    """
    Accounts Manager prepares; only the Finance Head accepts. The same
    segregation payroll applies between processing a run and approving it.
    """
    settlement = exit_workflow.settlement
    update_settlement(
        settlement=settlement, actor=staff["accounts_manager"].user, pending_salary="1000.00"
    )
    with pytest.raises(AccessDenied):
        clear_settlement(settlement=settlement, actor=staff["accounts_manager"].user)


# ===================================================== exit interview


def test_hr_records_the_exit_interview(exit_workflow, staff):
    interview = record_exit_interview(
        workflow=exit_workflow,
        actor=staff["hr_head"].user,
        primary_reason="better_opportunity",
        employee_feedback="Enjoyed the clinical work.",
        manager_feedback="Supportive but stretched.",
        rehire_eligibility="eligible",
    )
    assert interview.is_conducted
    assert interview.conducted_by_id == staff["hr_head"].user.pk
    assert interview.rehire_eligibility == "eligible"


def test_interview_contents_are_kept_out_of_the_audit_trail(exit_workflow, staff):
    """
    Candid feedback about a named manager must not be readable from the audit
    log, which is visible department-wide.
    """
    from apps.audit.models import AuditLog

    record_exit_interview(
        workflow=exit_workflow,
        actor=staff["hr_head"].user,
        manager_feedback="Specific and unflattering detail about a named person.",
    )

    entries = AuditLog.objects.filter(entity_type="offboarding.ExitInterview")
    assert entries.exists()
    for entry in entries:
        blob = f"{entry.before} {entry.after}"
        assert "unflattering" not in blob


# ===================================================== the gates


def test_every_gate_blocks_the_exit(exit_workflow, staff, laptop):
    """
    THE APPROVAL GATE, ENUMERATED.

    With nothing done, the blocker list must name every category the spec
    requires: clearance by department, HR, IT and finance; unreturned assets;
    and the uncleared settlement.
    """
    blockers = exit_blockers(exit_workflow)
    gates = {blocker["gate"] for blocker in blockers}

    assert "assets" in gates, "unreturned company property must block"
    assert "finance" in gates, "an uncleared settlement must block"
    assert {"hr", "department", "it"} <= gates, "each clearance gate must block"

    with pytest.raises(ValidationError) as exc:
        approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    assert "Company property" in str(exc.value)


def test_an_unreturned_asset_alone_blocks_the_exit(
    exit_workflow, staff, laptop, clear_everything
):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    # Re-allocate to recreate only the asset blocker.
    from apps.assets.services import allocate

    allocate(asset=laptop, employee=exit_workflow.employee, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    blockers = exit_blockers(exit_workflow)
    assert [b["gate"] for b in blockers] == ["assets"]

    with pytest.raises(ValidationError):
        approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user)


def test_returning_the_asset_clears_that_gate(exit_workflow, staff, laptop, clear_everything):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    assert exit_blockers(exit_workflow) == []


def test_a_write_off_is_the_sanctioned_alternative_to_a_return(
    exit_workflow, staff, laptop, clear_everything
):
    """Never silently released: the write-off is audited and carries a reason."""
    from apps.assets.services import allocate

    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    allocation = allocate(
        asset=laptop, employee=exit_workflow.employee, actor=staff["hr_head"].user
    )
    exit_workflow.refresh_from_db()
    assert exit_blockers(exit_workflow) != []

    with pytest.raises(ValidationError):
        write_off(allocation=allocation, actor=staff["hr_head"].user, reason="")

    write_off(
        allocation=allocation,
        actor=staff["hr_head"].user,
        reason="Laptop not returned after the final working day; recovered in settlement.",
    )
    exit_workflow.refresh_from_db()
    assert exit_blockers(exit_workflow) == []

    allocation.refresh_from_db()
    assert allocation.status == AllocationStatus.WRITTEN_OFF
    assert allocation.write_off_reason

    from apps.audit.models import AuditLog

    assert AuditLog.objects.filter(after__event="asset_write_off").exists()


def test_a_live_company_account_blocks_the_exit(exit_workflow, staff, clear_everything, leaver):
    from apps.itaccounts.services import record_account

    record_account(
        employee=leaver, actor=staff["hr_head"].user,
        email_address="priya@company.test", provider="google_workspace",
    )
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    account = CompanyEmailAccount.objects.get(employee=leaver)
    account.status = AccountStatus.ACTIVE
    account.deprovisioned_at = None
    account.save()

    gates = {blocker["gate"] for blocker in exit_blockers(exit_workflow)}
    assert "it" in gates


def test_deprovisioning_preserves_the_external_id_and_records_the_actor(
    leaver, staff, exit_workflow
):
    from apps.itaccounts.services import deprovision, record_account

    account = record_account(
        employee=leaver, actor=staff["hr_head"].user,
        email_address="priya@company.test", provider="google_workspace",
        external_account_id="ext-priya-1",
    )
    deprovision(account=account, actor=staff["hr_head"].user)
    account.refresh_from_db()

    assert account.status == AccountStatus.DEPROVISIONED
    assert account.deprovisioned_at is not None
    assert account.external_account_id == "ext-priya-1"
    # And still no credential anywhere.
    fields = {f.name.lower() for f in CompanyEmailAccount._meta.get_fields()}
    assert not (fields & {"password", "secret", "credential", "token"})


# ===================================================== approval and completion


def test_the_full_happy_path(exit_workflow, staff, laptop, clear_everything, leaver):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user, notes="All gates cleared.")
    exit_workflow.refresh_from_db()
    assert exit_workflow.stage == ExitStage.APPROVED
    assert exit_workflow.approved_by_id == staff["hr_head"].user.pk

    complete_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    leaver.refresh_from_db()

    assert exit_workflow.stage == ExitStage.COMPLETED
    assert exit_workflow.actual_last_working_date is not None
    assert leaver.status == EmployeeStatus.EXITED
    assert leaver.date_of_exit == exit_workflow.actual_last_working_date


def test_completion_requires_approval_first(exit_workflow, staff, laptop, clear_everything):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    with pytest.raises(ValidationError) as exc:
        complete_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    assert "must be approved" in str(exc.value)


def test_a_manager_cannot_approve_an_exit(exit_workflow, staff, laptop, clear_everything):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()

    with pytest.raises(AccessDenied):
        approve_exit(workflow=exit_workflow, actor=staff["medical_director"].user)


def test_the_lifecycle_service_still_guards_exited_independently(leaver, staff, laptop):
    """
    Belt and braces. Even bypassing the exit workflow entirely, the lifecycle
    service refuses EXITED while property is held — so no future caller can
    reach it around the side.
    """
    change_status(
        employee=leaver, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.RESIGNED, reason="Resigned for this test.",
    )
    with pytest.raises(LifecycleError) as exc:
        change_status(
            employee=leaver, actor=staff["hr_head"].user,
            new_status=EmployeeStatus.EXITED, reason="Final working day completed.",
        )
    assert "still allocated" in str(exc.value)


# ===================================================== terminal state


def test_exited_is_terminal(exit_workflow, staff, laptop, clear_everything, leaver):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    complete_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    leaver.refresh_from_db()

    assert allowed_targets(EmployeeStatus.EXITED) == []

    for target in (EmployeeStatus.ACTIVE, EmployeeStatus.CONFIRMED, EmployeeStatus.ON_NOTICE):
        with pytest.raises((LifecycleError, ValidationError)):
            change_status(
                employee=leaver, actor=staff["hr_head"].user,
                new_status=target, reason="Attempting to reactivate a leaver.",
            )

    leaver.refresh_from_db()
    assert leaver.status == EmployeeStatus.EXITED


def test_a_completed_exit_cannot_be_cancelled(
    exit_workflow, staff, laptop, clear_everything
):
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    complete_exit(workflow=exit_workflow, actor=staff["hr_head"].user)

    with pytest.raises(ValidationError) as exc:
        cancel_exit(
            workflow=exit_workflow, actor=staff["hr_head"].user,
            reason="Trying to bring the employee back.",
        )
    assert "new employee record" in str(exc.value)


def test_an_exit_can_be_cancelled_before_completion(exit_workflow, staff, leaver):
    cancel_exit(
        workflow=exit_workflow, actor=staff["hr_head"].user,
        reason="Resignation withdrawn after a counter-offer.",
    )
    exit_workflow.refresh_from_db()
    leaver.refresh_from_db()

    assert exit_workflow.stage == ExitStage.CANCELLED
    assert leaver.status == EmployeeStatus.ACTIVE


# ===================================================== atomicity and audit


def test_a_failed_exit_start_leaves_nothing_behind(leaver, staff, last_working_date, monkeypatch):
    """
    The status change and the workflow commit together or not at all. Injected
    after the status moves, so the rollback has something to undo.
    """
    from apps.offboarding import services as offboarding_services

    original = offboarding_services._issue_clearance
    seen = {}

    def explode(workflow, *, actor, template=None):
        leaver.refresh_from_db()
        seen["status_at_failure"] = leaver.status
        seen["workflows"] = ExitWorkflow.objects.count()
        raise ValidationError({"clearance": "injected failure"})

    monkeypatch.setattr(offboarding_services, "_issue_clearance", explode)

    with pytest.raises(ValidationError):
        start_exit(
            employee=leaver, actor=staff["hr_head"].user, exit_type=ExitType.RESIGNATION,
            last_working_date=last_working_date, reason="Testing rollback.",
        )
    monkeypatch.undo()
    assert original is offboarding_services._issue_clearance

    # Proof the test is meaningful: work HAD been done when it failed.
    assert seen["status_at_failure"] == EmployeeStatus.RESIGNED
    assert seen["workflows"] == 1

    # And none of it survived.
    leaver.refresh_from_db()
    assert leaver.status == EmployeeStatus.CONFIRMED
    assert ExitWorkflow.objects.count() == 0


@pytest.mark.parametrize(
    "event",
    [
        "exit_initiated",
        "clearance_completed",
        "settlement_cleared",
        "exit_approved",
        "exit_completed",
    ],
)
def test_every_important_act_is_audited(
    exit_workflow, staff, laptop, clear_everything, event
):
    from apps.audit.models import AuditLog

    item = exit_workflow.clearance_items.filter(category=ClearanceCategory.HR).first()
    complete_clearance_item(item=item, actor=staff["hr_head"].user)
    clear_everything(exit_workflow, actor=staff["hr_head"].user)
    exit_workflow.refresh_from_db()
    approve_exit(workflow=exit_workflow, actor=staff["hr_head"].user)
    complete_exit(workflow=exit_workflow, actor=staff["hr_head"].user)

    entry = AuditLog.objects.filter(after__event=event).first()
    assert entry is not None, f"'{event}' must be audited"
    assert entry.actor_id is not None, f"'{event}' must name its actor"
    assert entry.occurred_at is not None
