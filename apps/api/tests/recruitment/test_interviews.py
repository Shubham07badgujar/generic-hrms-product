"""
Interview scheduling, double-booking prevention, and feedback.

The spec requires double-booking to be blocked at three layers. Each is tested
in isolation, because they fail differently and a test that only exercises the
service would pass even if the database constraint had never been created —
which is exactly the state this file was written to catch.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.recruitment.models import Interview, InterviewStatus
from apps.recruitment.services.interviews import (
    SchedulingConflict,
    find_conflict,
    reschedule_interview,
    schedule_interview,
    submit_feedback,
)
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

SLOT = timezone.now() + dt.timedelta(days=3)


def _stage(job, order: int):
    return job.workflow.stages.get(order=order)


def _ready(application, at_stage, order=30):
    """An application parked at an interview stage, HR-verified."""
    return at_stage(application, order, verified=True)


# ---------------------------------------------------------------------------
# Layer 2 — the service
# ---------------------------------------------------------------------------


def test_an_interviewer_cannot_be_booked_twice_at_the_same_time(
    therapist_job, make_application, at_stage, staff
):
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="a@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="b@example.test"), at_stage)

    schedule_interview(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT, duration_minutes=45,
    )

    with pytest.raises(SchedulingConflict) as exc:
        schedule_interview(
            application=second, stage=stage, interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user,
            scheduled_at=SLOT + dt.timedelta(minutes=20),  # overlaps by 25 minutes
            duration_minutes=45,
        )

    # The message must name the clash, not just refuse.
    assert "already has an interview" in str(exc.value)
    assert Interview.objects.count() == 1


def test_back_to_back_interviews_are_allowed(
    therapist_job, make_application, at_stage, staff
):
    """
    Half-open [start, end). An interview ending at 10:45 must not block one
    beginning at 10:45 — otherwise a full interview day becomes unschedulable.
    """
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="c@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="d@example.test"), at_stage)

    schedule_interview(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT, duration_minutes=45,
    )
    schedule_interview(
        application=second, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user,
        scheduled_at=SLOT + dt.timedelta(minutes=45),
        duration_minutes=45,
    )

    assert Interview.objects.count() == 2


def test_a_cancelled_interview_frees_the_slot(
    therapist_job, make_application, at_stage, staff
):
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="e@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="f@example.test"), at_stage)

    booked = schedule_interview(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    booked.status = InterviewStatus.CANCELLED
    booked.save()

    schedule_interview(
        application=second, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    assert Interview.objects.filter(status=InterviewStatus.SCHEDULED).count() == 1


def test_two_interviewers_may_hold_interviews_at_the_same_time(
    therapist_job, make_application, at_stage, staff
):
    """The constraint is per interviewer, not global."""
    first = _ready(make_application(therapist_job, email="g@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="h@example.test"), at_stage, 40)

    schedule_interview(
        application=first, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    schedule_interview(
        application=second, stage=_stage(therapist_job, 40),
        interviewer=staff["senior_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    assert Interview.objects.count() == 2


def test_rescheduling_re_runs_the_conflict_check(
    therapist_job, make_application, at_stage, staff
):
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="i@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="j@example.test"), at_stage)

    schedule_interview(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    movable = schedule_interview(
        application=second, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT + dt.timedelta(hours=4),
    )

    with pytest.raises(SchedulingConflict):
        reschedule_interview(
            interview=movable, actor=staff["hr_head"].user,
            scheduled_at=SLOT + dt.timedelta(minutes=10),
        )


def test_rescheduling_an_interview_does_not_conflict_with_itself(
    therapist_job, make_application, at_stage, staff
):
    """Moving an interview by five minutes overlaps its own old slot."""
    application = _ready(make_application(therapist_job, email="k@example.test"), at_stage)
    interview = schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )

    moved = reschedule_interview(
        interview=interview, actor=staff["hr_head"].user,
        scheduled_at=SLOT + dt.timedelta(minutes=5),
    )
    assert moved.status == InterviewStatus.RESCHEDULED


# ---------------------------------------------------------------------------
# Layer 1 — the database
# ---------------------------------------------------------------------------


def test_the_database_itself_refuses_an_overlap(
    therapist_job, make_application, at_stage, staff
):
    """
    Bypass the service entirely and write straight to the ORM.

    This is what a concurrent request effectively does: both pass the service's
    check, then both try to commit. If this test passes only because of the
    Python check, the system has a race — so the check is skipped here.
    """
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="l@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="m@example.test"), at_stage)

    Interview.objects.create(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        scheduled_at=SLOT, duration_minutes=60,
    )

    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():
            Interview.objects.create(
                application=second, stage=stage, interviewer=staff["clinic_doctor"],
                scheduled_at=SLOT + dt.timedelta(minutes=30), duration_minutes=60,
            )

    assert "excl_interviewer_double_booking" in str(exc.value)


def test_the_database_permits_back_to_back_at_the_boundary(
    therapist_job, make_application, at_stage, staff
):
    """The DB constraint must use the same half-open bounds as the service."""
    stage = _stage(therapist_job, 30)
    first = _ready(make_application(therapist_job, email="n@example.test"), at_stage)
    second = _ready(make_application(therapist_job, email="o@example.test"), at_stage)

    Interview.objects.create(
        application=first, stage=stage, interviewer=staff["clinic_doctor"],
        scheduled_at=SLOT, duration_minutes=30,
    )
    Interview.objects.create(
        application=second, stage=stage, interviewer=staff["clinic_doctor"],
        scheduled_at=SLOT + dt.timedelta(minutes=30), duration_minutes=30,
    )
    assert Interview.objects.count() == 2


# ---------------------------------------------------------------------------
# Layer 3 — the conflict lookup the API exposes
# ---------------------------------------------------------------------------


def test_find_conflict_reports_the_clash_the_form_would_hit(
    therapist_job, make_application, at_stage, staff
):
    application = _ready(make_application(therapist_job, email="p@example.test"), at_stage)
    schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT, duration_minutes=45,
    )

    clash = find_conflict(
        interviewer=staff["clinic_doctor"],
        start=SLOT + dt.timedelta(minutes=15),
        end=SLOT + dt.timedelta(minutes=60),
    )
    assert clash is not None

    clear = find_conflict(
        interviewer=staff["clinic_doctor"],
        start=SLOT + dt.timedelta(hours=2),
        end=SLOT + dt.timedelta(hours=3),
    )
    assert clear is None


# ---------------------------------------------------------------------------
# Who may be booked, and who may report
# ---------------------------------------------------------------------------


def test_an_interviewer_without_the_stage_role_cannot_be_booked(
    therapist_job, make_application, at_stage, staff
):
    """A Clinic Doctor must not fill the Senior Doctor round."""
    application = _ready(make_application(therapist_job), at_stage, 40)

    with pytest.raises(ValidationError) as exc:
        schedule_interview(
            application=application, stage=_stage(therapist_job, 40),
            interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user, scheduled_at=SLOT,
        )
    assert "does not hold" in str(exc.value)


def test_a_non_interview_stage_cannot_be_scheduled(
    therapist_job, make_application, at_stage, staff
):
    application = _ready(make_application(therapist_job), at_stage, 20)

    with pytest.raises(ValidationError) as exc:
        schedule_interview(
            application=application, stage=_stage(therapist_job, 20),
            interviewer=staff["hr_manager"],
            actor=staff["hr_head"].user, scheduled_at=SLOT,
        )
    assert "not an interview stage" in str(exc.value)


def test_a_closed_application_cannot_be_scheduled(
    therapist_job, make_application, at_stage, staff
):
    from apps.recruitment.models import ApplicationStatus

    application = _ready(make_application(therapist_job), at_stage)
    application.status = ApplicationStatus.REJECTED
    application.save(update_fields=["status"])

    with pytest.raises(ValidationError) as exc:
        schedule_interview(
            application=application, stage=_stage(therapist_job, 30),
            interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user, scheduled_at=SLOT,
        )
    assert "closed" in str(exc.value)


def test_only_the_assigned_interviewer_may_submit_feedback(
    therapist_job, make_application, at_stage, staff, roles
):
    """
    Holding the role is not enough.

    Two Clinic Doctors both clear the RBAC check and both match the stage's
    responsible role; only the one actually booked may report on the meeting
    they attended.
    """
    import datetime as _dt

    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    other_user = User.objects.create_user(
        email="other.doctor@example.test", password="test-password-12345", first_name="Nikhil"
    )
    UserRole.objects.create(user=other_user, role=roles["clinic_doctor"])
    bind_membership(other_user)
    Employee.objects.create(
        employee_code="EMP09999", user=other_user, first_name="Nikhil", last_name="Rao",
        department=staff["clinic_doctor"].department,
        location=staff["clinic_doctor"].location,
        date_of_joining=_dt.date(2021, 1, 1),
    )

    application = _ready(make_application(therapist_job), at_stage)
    interview = schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )

    with pytest.raises(ValidationError) as exc:
        submit_feedback(
            interview=interview, actor=other_user,
            answers={}, recommendation="hire",
        )
    assert "assigned interviewer" in str(exc.value)


def test_feedback_requires_every_mandatory_field(
    therapist_job, make_application, at_stage, staff
):
    application = _ready(make_application(therapist_job), at_stage)
    interview = schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )

    with pytest.raises(ValidationError) as exc:
        submit_feedback(
            interview=interview, actor=staff["clinic_doctor"].user,
            answers={"communication": 4},  # the rest missing
            recommendation="hire",
        )
    assert "clinical_knowledge" in str(exc.value.message_dict)


def test_feedback_rejects_an_out_of_range_rating(
    therapist_job, make_application, at_stage, staff
):
    application = _ready(make_application(therapist_job), at_stage)
    interview = schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )
    stage = _stage(therapist_job, 30)
    answers = {f.key: 3 for f in stage.feedback_form.fields.filter(is_required=True)}
    answers["communication"] = 9

    with pytest.raises(ValidationError) as exc:
        submit_feedback(
            interview=interview, actor=staff["clinic_doctor"].user,
            answers=answers, recommendation="hire",
        )
    assert "between 1 and 5" in str(exc.value)


def test_feedback_cannot_be_submitted_twice(
    therapist_job, make_application, at_stage, staff, completed_interview
):
    application = _ready(make_application(therapist_job), at_stage)
    interview = completed_interview(application, 30, "clinic_doctor")
    stage = _stage(therapist_job, 30)
    answers = {f.key: 3 for f in stage.feedback_form.fields.filter(is_required=True)}

    with pytest.raises(ValidationError) as exc:
        submit_feedback(
            interview=interview, actor=staff["clinic_doctor"].user,
            answers=answers, recommendation="hire",
        )
    assert "already been submitted" in str(exc.value)


def test_submitting_feedback_completes_the_interview(
    therapist_job, make_application, at_stage, completed_interview
):
    application = _ready(make_application(therapist_job), at_stage)
    interview = completed_interview(application, 30, "clinic_doctor")
    interview.refresh_from_db()
    assert interview.status == InterviewStatus.COMPLETED


def test_a_completed_interview_stops_blocking_the_calendar(
    therapist_job, make_application, at_stage, staff, completed_interview
):
    """Yesterday's finished interview must not block tomorrow's booking."""
    first = _ready(make_application(therapist_job, email="q@example.test"), at_stage)
    done = completed_interview(first, 30, "clinic_doctor")

    second = _ready(make_application(therapist_job, email="r@example.test"), at_stage)
    schedule_interview(
        application=second, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=done.scheduled_at,
    )
    assert Interview.objects.count() == 2


# ---------------------------------------------------------------------------
# Who is eligible to take a round at all
# ---------------------------------------------------------------------------


def test_eligible_interviewers_come_from_the_stage_not_the_directory(
    therapist_job, staff
):
    """The pool is the stage's role, whoever is asking."""
    from apps.recruitment.services.interviews import eligible_interviewers

    stage = _stage(therapist_job, 40)  # the Senior Doctor round
    pool = list(eligible_interviewers(stage))

    assert staff["senior_doctor"] in pool
    assert staff["clinic_doctor"] not in pool


def test_a_former_employee_is_neither_offered_nor_bookable(
    therapist_job, make_application, at_stage, staff
):
    """
    The round's only role-holder having left is the situation that made a
    round unschedulable with no explanation. The pool drops them, the
    service refuses them, and the message says what to do about it.
    """
    from apps.employees.models import EmployeeStatus
    from apps.recruitment.services.interviews import (
        eligible_interviewers,
        interviewer_pool_problem,
    )

    stage = _stage(therapist_job, 40)
    doctor = staff["senior_doctor"]
    doctor.status = EmployeeStatus.TERMINATED
    doctor.save(update_fields=["status"])

    assert doctor not in list(eligible_interviewers(stage))

    problem = interviewer_pool_problem(stage)
    assert "has left the organisation" in problem
    assert doctor.full_name in problem

    application = _ready(make_application(therapist_job), at_stage, 40)
    with pytest.raises(ValidationError) as exc:
        schedule_interview(
            application=application, stage=stage, interviewer=doctor,
            actor=staff["hr_head"].user, scheduled_at=SLOT,
        )
    assert "has left the organisation" in str(exc.value)


def test_an_unstaffed_role_says_so_rather_than_blaming_the_scheduler(
    therapist_job, staff
):
    from apps.recruitment.services.interviews import interviewer_pool_problem

    stage = _stage(therapist_job, 40)
    staff["senior_doctor"].user.user_roles.update(is_active=False)

    problem = interviewer_pool_problem(stage)
    assert "Nobody holds" in problem
    assert stage.responsible_role.name in problem


def test_a_fully_staffed_round_reports_no_problem(therapist_job):
    from apps.recruitment.services.interviews import interviewer_pool_problem

    assert interviewer_pool_problem(_stage(therapist_job, 40)) == ""


# ---------------------------------------------------------------------------
# The candidate's side of double-booking
# ---------------------------------------------------------------------------


def test_a_round_cannot_hold_two_live_interviews(
    therapist_job, make_application, at_stage, staff
):
    """
    Booking a round twice used to leave two open interviews: two invitations
    to the candidate, two interviewers each expecting to meet them, and no
    answer to whose feedback the round waits on.
    """
    stage = _stage(therapist_job, 30)
    application = _ready(make_application(therapist_job), at_stage)

    schedule_interview(
        application=application, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=SLOT,
    )

    with pytest.raises(SchedulingConflict) as exc:
        schedule_interview(
            application=application, stage=stage, interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user, scheduled_at=SLOT + dt.timedelta(hours=4),
        )
    assert "already has a" in str(exc.value)
    assert Interview.objects.filter(
        application=application, stage=stage,
        status__in=("scheduled", "confirmed", "rescheduled"),
    ).count() == 1


def test_a_candidate_cannot_be_in_two_interviews_at_once(
    therapist_job, make_application, at_stage, staff
):
    """The mirror of the interviewer rule: one person, one place at a time."""
    application = _ready(make_application(therapist_job), at_stage)
    schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"], actor=staff["hr_head"].user,
        scheduled_at=SLOT,
    )

    # A different round, a different interviewer — the same candidate, the
    # same half hour.
    at_stage(application, 40, verified=True)
    with pytest.raises(SchedulingConflict) as exc:
        schedule_interview(
            application=application, stage=_stage(therapist_job, 40),
            interviewer=staff["senior_doctor"], actor=staff["hr_head"].user,
            scheduled_at=SLOT + dt.timedelta(minutes=15),
        )
    assert "is already in a" in str(exc.value)


def test_rescheduling_will_not_collide_with_the_candidates_other_round(
    therapist_job, make_application, at_stage, staff
):
    application = _ready(make_application(therapist_job), at_stage)
    first = schedule_interview(
        application=application, stage=_stage(therapist_job, 30),
        interviewer=staff["clinic_doctor"], actor=staff["hr_head"].user,
        scheduled_at=SLOT,
    )
    at_stage(application, 40, verified=True)
    second = schedule_interview(
        application=application, stage=_stage(therapist_job, 40),
        interviewer=staff["senior_doctor"], actor=staff["hr_head"].user,
        scheduled_at=SLOT + dt.timedelta(days=1),
    )

    with pytest.raises(SchedulingConflict):
        reschedule_interview(
            interview=second, actor=staff["hr_head"].user,
            scheduled_at=first.scheduled_at + dt.timedelta(minutes=10),
        )
