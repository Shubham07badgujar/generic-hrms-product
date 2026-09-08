"""
Cutover gate 6b: the governance paths.

The lifecycle test proves the happy path works. These prove the controls around
it hold — rejection with a mandatory reason, the two-level decision, the admin
override and its trail, double-booking, and the downstream records a hire
produces.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

pytestmark = pytest.mark.django_db


# ===================================================== two-level rejection


def test_a_department_head_recommends_and_does_not_reject(
    therapist_job, make_application, staff, at_stage
):
    """
    The design's central claim.

    A department recommendation must leave the candidate ACTIVE and route to
    HR. If it terminated the application, the two-level design would be
    decorative.
    """
    from apps.recruitment.models import ApplicationStatus
    from apps.recruitment.services.engine import record_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 50)

    record_decision(
        application=application,
        actor=staff["medical_director"].user,
        decision=Decision.RECOMMEND_REJECT,
        rationale="Clinical depth was not sufficient for this role.",
    )
    application.refresh_from_db()

    assert application.status == ApplicationStatus.ACTIVE, (
        "A department recommendation terminated the application."
    )
    assert application.current_stage.is_final_hr_decision, (
        "The recommendation did not route to HR's decision stage."
    )


def test_the_final_rejection_demands_a_substantial_reason(
    therapist_job, make_application, staff, at_stage
):
    """
    Mandatory, and enforced at the service — not only in a form.

    A rejection is permanent and the candidate is entitled to a real reason.
    """
    from apps.recruitment.services.engine import record_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 60)

    with pytest.raises(ValidationError):
        record_decision(
            application=application,
            actor=staff["hr_head"].user,
            decision=Decision.REJECT,
            rationale="no",
        )


def test_hr_rejection_is_recorded_with_its_reason_and_a_history_snapshot(
    therapist_job, make_application, staff, at_stage
):
    from apps.recruitment.models import ApplicationStatus, CandidateRejection
    from apps.recruitment.services.engine import record_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 60)
    reason = "Insufficient clinical experience for an unsupervised caseload."

    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale=reason,
    )
    application.refresh_from_db()

    rejection = CandidateRejection.objects.get(application=application)

    assert application.status == ApplicationStatus.REJECTED
    assert rejection.reason == reason
    assert rejection.rejected_by == staff["hr_head"].user
    # The snapshot is what keeps the justification readable after the feedback
    # it rested on is edited.
    assert rejection.history_snapshot


# ========================================================= admin override


def test_an_override_never_deletes_the_rejection_it_reverses(
    therapist_job, make_application, staff, at_stage, admin_user
):
    """
    The original decision survives.

    An override that overwrote the rejection would leave no evidence that HR
    ever decided — which is exactly what a reviewer needs to see.
    """
    from apps.recruitment.models import ApplicationStatus, CandidateRejection
    from apps.recruitment.services.engine import record_decision
    from apps.recruitment.services.hiring import override_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Rejected on the strength of the clinical rounds.",
    )
    rejection = CandidateRejection.objects.get(application=application)

    override_decision(
        application=application,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason="Reopened after the department reconsidered the second round.",
    )

    rejection.refresh_from_db()
    assert rejection.pk is not None, "The override deleted the original rejection."
    assert rejection.is_overridden is True


def test_the_override_is_audited_with_actor_reason_and_both_states(
    therapist_job, make_application, staff, at_stage, admin_user
):
    """OVERRIDE is its own audit verb precisely so it can be found."""
    from apps.audit.models import AuditAction, AuditLog
    from apps.recruitment.models import ApplicationStatus
    from apps.recruitment.services.engine import record_decision
    from apps.recruitment.services.hiring import override_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Rejected after the second clinical round.",
    )

    reason = "Reopened: the panel's concern was addressed by later evidence."
    override_decision(
        application=application,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=reason,
    )

    entry = AuditLog.objects.filter(action=AuditAction.OVERRIDE).order_by("-id").first()

    assert entry is not None, "The override was not audited."
    assert entry.actor == admin_user
    assert reason in (entry.reason or "") or reason in str(entry.after)
    assert entry.before, "The override did not record the previous state."
    assert entry.after, "The override did not record the new state."


def test_the_reopened_stage_comes_from_the_workflow_not_a_constant(
    therapist_job, make_application, staff, at_stage, admin_user
):
    """
    An override must land on a stage the configured workflow actually has.

    Hard-coding one would break the moment a workflow was reconfigured, and the
    application would sit on a stage that no longer exists.
    """
    from apps.recruitment.models import ApplicationStatus
    from apps.recruitment.services.engine import record_decision
    from apps.recruitment.services.hiring import override_decision
    from apps.workflows.models import Decision

    application = at_stage(make_application(therapist_job), 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Rejected pending further review.",
    )

    override_decision(
        application=application,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason="Reopening for a further look at the clinical evidence.",
    )
    application.refresh_from_db()

    workflow_stages = set(
        therapist_job.workflow.stages.values_list("id", flat=True)
    )
    assert application.current_stage_id in workflow_stages
    assert not application.current_stage.is_terminal, (
        "The application was reopened onto a terminal stage."
    )


# ================================================ interview double-booking


def test_an_interviewer_cannot_be_booked_twice_at_once(
    therapist_job, make_application, staff, at_stage
):
    """
    Enforced in the database, not merely in the form.

    The service checks first so the user gets a readable error, but the
    exclusion constraint is what makes it true under concurrency.
    """
    from apps.recruitment.services.interviews import schedule_interview

    first = at_stage(make_application(therapist_job, email="a@x.test"), 30)
    second = at_stage(make_application(therapist_job, email="b@x.test"), 30)
    when = timezone.now() + dt.timedelta(days=3)

    schedule_interview(
        application=first,
        stage=first.current_stage,
        actor=staff["hr_head"].user,
        interviewer=staff["clinic_doctor"],
        scheduled_at=when,
        duration_minutes=60,
    )

    with pytest.raises(Exception) as exc:
        schedule_interview(
            application=second,
            stage=second.current_stage,
            actor=staff["hr_head"].user,
            interviewer=staff["clinic_doctor"],
            scheduled_at=when + dt.timedelta(minutes=30),  # overlaps
            duration_minutes=60,
        )

    assert "already" in str(exc.value).lower() or "conflict" in str(exc.value).lower()


def test_the_database_itself_refuses_an_overlap(
    therapist_job, make_application, staff, at_stage
):
    """
    Bypassing the service must still fail.

    This is the assertion that proves the guarantee is structural: the
    exclusion constraint exists and is active, so a concurrent request that
    slipped past the service check cannot double-book anyone.
    """
    from django.db import IntegrityError, transaction

    from apps.recruitment.models import Interview, InterviewStatus

    first = at_stage(make_application(therapist_job, email="c@x.test"), 30)
    second = at_stage(make_application(therapist_job, email="d@x.test"), 30)
    when = timezone.now() + dt.timedelta(days=5)

    Interview.objects.create(
        application=first,
        stage=first.current_stage,
        interviewer=staff["senior_doctor"],
        scheduled_at=when,
        duration_minutes=60,
        status=InterviewStatus.SCHEDULED,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Interview.objects.create(
                application=second,
                stage=second.current_stage,
                interviewer=staff["senior_doctor"],
                scheduled_at=when + dt.timedelta(minutes=15),
                duration_minutes=60,
                status=InterviewStatus.SCHEDULED,
            )


# ============================================ downstream records of a hire


def test_a_hire_produces_an_auditable_trail(
    therapist_job, make_application, drive_to_selection, staff, org, onboarding_config
):
    """
    Conversion is one transaction producing several records.

    Asserted together because the value is that they are consistent — an
    employee with no login, or a login with no audit entry, is a broken hire
    even though each row individually looks fine.
    """
    from decimal import Decimal

    from apps.audit.models import AuditLog
    from apps.recruitment.services import hiring

    application = drive_to_selection(
        make_application(therapist_job, email="trail@x.test"),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )
    offer = hiring.create_offer(
        application=application,
        actor=staff["hr_head"].user,
        joining_date=timezone.localdate() + dt.timedelta(days=15),
        offered_ctc=Decimal("400000.00"),
        level=org["levels"][5],
    )
    hiring.send_offer(offer=offer, actor=staff["hr_head"].user)
    hiring.record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)

    result = hiring.convert_to_employee(
        application=application,
        actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    employee = result.employee

    assert employee.user is not None, "Hire produced no login."
    assert employee.user.user_roles.exists(), "Hire produced a login with no role."
    assert getattr(employee, "onboarding", None) is not None
    assert employee.created_from_candidate_id == application.candidate_id

    assert AuditLog.objects.filter(
        entity_type="employees.Employee", entity_id=str(employee.pk)
    ).exists(), "The hire was not audited."


def test_a_conversion_that_fails_leaves_nothing_behind(
    therapist_job, make_application, drive_to_selection, staff, org, onboarding_config
):
    """
    Atomicity, asserted by breaking it deliberately.

    A partial hire — a User with no Employee — is the failure mode the
    employee-first design exists to make impossible.
    """
    from decimal import Decimal

    from apps.accounts.models import User
    from apps.recruitment.services import hiring

    application = drive_to_selection(
        make_application(therapist_job, email="rollback@x.test"),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )
    offer = hiring.create_offer(
        application=application,
        actor=staff["hr_head"].user,
        joining_date=timezone.localdate() + dt.timedelta(days=15),
        offered_ctc=Decimal("400000.00"),
        level=org["levels"][5],
    )
    hiring.send_offer(offer=offer, actor=staff["hr_head"].user)
    hiring.record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)

    before = User.objects.count()

    with pytest.raises(Exception):
        hiring.convert_to_employee(
            application=application,
            actor=staff["hr_head"].user,
            reporting_manager=staff["medical_director"],
            # A department the target role may not be placed into: the
            # hierarchy check fires after the User would have been created.
            department=org["departments"]["finance"],
        )

    assert User.objects.count() == before, (
        "A failed conversion left a User behind — the transaction did not roll back."
    )
