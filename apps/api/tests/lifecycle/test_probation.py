"""
Probation.

The load-bearing test in this file is
`test_the_sweep_never_confirms_anybody`. Everything else describes how a
decision is made; that one asserts the system does not make it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.employees.models import (
    EmployeeStatus,
    ProbationDecision,
    ProbationReview,
    ProbationStatus,
)
from apps.employees.services.probation import (
    decide,
    due_for_reminder,
    record_assessment,
    run_reminder_sweep,
)
from apps.onboarding.models import EmployeeLetter, LetterType
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

RATIONALE = "Performance in the clinical rounds has not yet met the required standard."


@pytest.fixture
def on_probation(employee):
    """A hire whose probation ends in three weeks."""
    employee.probation_end_date = timezone.localdate() + dt.timedelta(days=21)
    employee.probation_status = ProbationStatus.ACTIVE
    employee.status = EmployeeStatus.ON_PROBATION
    employee.save()
    return employee


@pytest.fixture
def overdue(employee):
    employee.probation_end_date = timezone.localdate() - dt.timedelta(days=3)
    employee.probation_status = ProbationStatus.ACTIVE
    employee.status = EmployeeStatus.ON_PROBATION
    employee.save()
    return employee


@pytest.fixture
def review(on_probation, staff):
    return ProbationReview.objects.create(
        employee=on_probation,
        probation_end_date=on_probation.probation_end_date,
        reviewer=staff["medical_director"],
    )


# ===================================================== setup


def test_a_new_hire_starts_on_probation(employee):
    """Set by `create_employee`, in the same transaction as the hire."""
    assert employee.probation_status == ProbationStatus.ACTIVE
    assert employee.probation_start_date == employee.date_of_joining
    assert employee.probation_end_date > employee.date_of_joining
    assert employee.status == EmployeeStatus.ON_PROBATION


# ===================================================== reminders


def test_reminders_are_grouped_by_how_close_the_date_is(on_probation, overdue, staff):
    buckets = due_for_reminder()
    ids = {key: {e.pk for e in value} for key, value in buckets.items()}

    # `overdue` and `on_probation` are the same employee across two fixtures in
    # separate tests; here only one is loaded, so assert on membership shape.
    assert set(buckets) == {"t_minus_30", "t_minus_7", "overdue"}
    assert any(ids.values())


def test_the_sweep_opens_a_review_and_flags_the_reminder(on_probation):
    counts = run_reminder_sweep()

    assert counts["reviews_opened"] == 1
    review = ProbationReview.objects.get(employee=on_probation)
    assert review.decision == ProbationDecision.PENDING
    assert review.notified_30d is True


def test_the_sweep_marks_an_overdue_probation_due(overdue):
    run_reminder_sweep()
    overdue.refresh_from_db()

    assert overdue.probation_status == ProbationStatus.DUE
    # DUE is a prompt, not an outcome — employment status is untouched.
    assert overdue.status == EmployeeStatus.ON_PROBATION


def test_the_sweep_never_confirms_anybody(overdue):
    """
    THE RULE THIS MODULE EXISTS TO HOLD.

    The probation ended days ago and the sweep runs repeatedly. Nobody is
    confirmed, no confirmation date is written, and no confirmation letter is
    produced. Only an HR decision can do any of that.
    """
    for _ in range(5):
        run_reminder_sweep()

    overdue.refresh_from_db()
    assert overdue.probation_status != ProbationStatus.CONFIRMED
    assert overdue.status != EmployeeStatus.CONFIRMED
    assert overdue.confirmation_date is None
    assert not EmployeeLetter.objects.filter(
        employee=overdue, letter_type=LetterType.CONFIRMATION
    ).exists()

    review = ProbationReview.objects.get(employee=overdue)
    assert review.decision == ProbationDecision.PENDING
    assert review.decided_by is None


def test_the_sweep_is_idempotent(on_probation):
    run_reminder_sweep()
    second = run_reminder_sweep()

    assert second["reviews_opened"] == 0
    assert second["reminded_30d"] == 0
    assert ProbationReview.objects.filter(employee=on_probation).count() == 1


# ===================================================== the assessment


def test_a_manager_records_an_assessment_and_a_recommendation(review, staff):
    record_assessment(
        review=review,
        actor=staff["medical_director"].user,
        recommendation=ProbationDecision.CONFIRM,
        performance_rating=4,
        reliability_rating=5,
        strengths="Strong with patients.",
        areas_for_improvement="Note-keeping.",
    )
    review.refresh_from_db()

    assert review.recommendation == ProbationDecision.CONFIRM
    assert review.reviewer_id == staff["medical_director"].pk
    assert review.reviewed_at is not None
    # ADVISORY: nothing about the employee has changed.
    assert review.decision == ProbationDecision.PENDING
    review.employee.refresh_from_db()
    assert review.employee.probation_status == ProbationStatus.ACTIVE


def test_a_manager_cannot_make_the_decision(review, staff):
    """
    The same split recruitment draws: the manager recommends, HR decides.
    A Medical Director holds PROBATION_REVIEW/EDIT but never DECIDE.
    """
    with pytest.raises(AccessDenied):
        decide(
            review=review,
            actor=staff["medical_director"].user,
            decision=ProbationDecision.CONFIRM,
        )

    review.refresh_from_db()
    assert review.decision == ProbationDecision.PENDING


# ===================================================== HR's decision


def test_hr_confirms_and_the_letter_follows(review, staff, lifecycle_config):
    decide(review=review, actor=staff["hr_head"].user, decision=ProbationDecision.CONFIRM)

    review.refresh_from_db()
    employee = review.employee
    employee.refresh_from_db()

    assert review.decision == ProbationDecision.CONFIRM
    assert review.decided_by_id == staff["hr_head"].user.pk
    assert review.decided_at is not None

    assert employee.probation_status == ProbationStatus.CONFIRMED
    assert employee.status == EmployeeStatus.CONFIRMED
    assert employee.confirmation_date == timezone.localdate()

    # The letter is generated BECAUSE the decision exists, and only after it.
    assert review.confirmation_letter is not None
    assert review.confirmation_letter.letter_type == LetterType.CONFIRMATION
    assert review.confirmation_letter.pdf_file


def test_confirmation_needs_no_rationale(review, staff, lifecycle_config):
    """A confirmation needs no defence; the other two decisions do."""
    decide(review=review, actor=staff["hr_head"].user, decision=ProbationDecision.CONFIRM)
    review.refresh_from_db()
    assert review.decision == ProbationDecision.CONFIRM


@pytest.mark.parametrize("decision", [ProbationDecision.EXTEND, ProbationDecision.TERMINATE])
def test_extending_or_terminating_demands_a_written_rationale(review, staff, decision):
    with pytest.raises(ValidationError) as exc:
        decide(
            review=review,
            actor=staff["hr_head"].user,
            decision=decision,
            rationale="too short",
            extended_to=timezone.localdate() + dt.timedelta(days=90),
        )
    assert "rationale" in str(exc.value)

    review.refresh_from_db()
    assert review.decision == ProbationDecision.PENDING


def test_extending_moves_the_end_date_and_keeps_them_on_probation(review, staff):
    new_date = review.probation_end_date + dt.timedelta(days=60)

    decide(
        review=review,
        actor=staff["hr_head"].user,
        decision=ProbationDecision.EXTEND,
        rationale=RATIONALE,
        extended_to=new_date,
    )

    review.refresh_from_db()
    employee = review.employee
    employee.refresh_from_db()

    assert review.extended_to == new_date
    assert employee.probation_end_date == new_date
    assert employee.probation_status == ProbationStatus.ACTIVE
    assert employee.status == EmployeeStatus.ON_PROBATION
    assert employee.confirmation_date is None


def test_an_extension_must_move_the_date_forward(review, staff):
    with pytest.raises(ValidationError) as exc:
        decide(
            review=review,
            actor=staff["hr_head"].user,
            decision=ProbationDecision.EXTEND,
            rationale=RATIONALE,
            extended_to=review.probation_end_date - dt.timedelta(days=1),
        )
    assert "later" in str(exc.value)


def test_terminating_ends_the_employment(review, staff):
    decide(
        review=review,
        actor=staff["hr_head"].user,
        decision=ProbationDecision.TERMINATE,
        rationale=RATIONALE,
    )

    employee = review.employee
    employee.refresh_from_db()
    assert employee.probation_status == ProbationStatus.TERMINATED
    assert employee.status == EmployeeStatus.TERMINATED
    assert employee.confirmation_date is None


def test_a_decided_review_cannot_be_rewritten(review, staff, lifecycle_config):
    """
    History is appended to, not edited. A second opinion is a new review.
    """
    decide(review=review, actor=staff["hr_head"].user, decision=ProbationDecision.CONFIRM)

    with pytest.raises(ValidationError) as exc:
        decide(
            review=review,
            actor=staff["hr_head"].user,
            decision=ProbationDecision.TERMINATE,
            rationale=RATIONALE,
        )
    assert "already decided" in str(exc.value)


def test_the_decision_is_audited_with_both_states(review, staff, lifecycle_config):
    from apps.audit.models import AuditLog

    decide(review=review, actor=staff["hr_head"].user, decision=ProbationDecision.CONFIRM)

    entry = AuditLog.objects.filter(
        entity_type="employees.ProbationReview",
        entity_id=str(review.pk),
        after__event="probation_decision",
    ).first()

    assert entry is not None
    assert entry.actor_id == staff["hr_head"].user.pk
    assert entry.after["decision"] == ProbationDecision.CONFIRM
    assert entry.after["status"] == EmployeeStatus.CONFIRMED
    assert entry.before["status"] == EmployeeStatus.ON_PROBATION


# ===================================================== the letter gate


def test_a_confirmation_letter_cannot_be_issued_before_hr_confirms(
    on_probation, staff, lifecycle_config
):
    """
    The letter would state something untrue.

    Enforced in the letter service too, so the letter endpoint cannot be used
    to reach round the back of the probation decision.
    """
    from apps.onboarding.letters import LetterError, generate_letter

    with pytest.raises(LetterError) as exc:
        generate_letter(
            employee=on_probation,
            actor=staff["hr_head"].user,
            letter_type=LetterType.CONFIRMATION,
        )
    assert "after HR has recorded a probation confirmation" in str(exc.value)

    assert not EmployeeLetter.objects.filter(
        employee=on_probation, letter_type=LetterType.CONFIRMATION
    ).exists()


def test_the_letter_becomes_available_once_hr_confirms(review, staff, lifecycle_config):
    from apps.onboarding.letters import generate_letter

    decide(
        review=review,
        actor=staff["hr_head"].user,
        decision=ProbationDecision.CONFIRM,
        generate_letter=False,
    )
    review.employee.refresh_from_db()

    letter = generate_letter(
        employee=review.employee,
        actor=staff["hr_head"].user,
        letter_type=LetterType.CONFIRMATION,
    )
    assert letter.pdf_file
    assert letter.template_version >= 1
