"""
A department head who interviews outside their own department.

Scopes merge by breadth, so someone who is both a department head and an
interviewer holds DEPARTMENT where a pure interviewer holds SELF. The
department filter used to REPLACE the assigned-rows filter, so the broader
authority silently took away the rows they were personally booked on: a
Medical Director on a Sales interview could open the interview and file the
feedback, then get a 404 recording the decision.

Found by driving every job's configured workflow end to end — the Medical
Director round is the only one in this organisation that routes a department
head onto another department's pipeline.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.recruitment.access import pipeline_scope_filter
from apps.recruitment.models import Application, Interview
from core.access import Action, Resource

pytestmark = pytest.mark.django_db


def _first_interview_stage(job):
    from apps.workflows.models import StageKind

    return job.workflow.stages.filter(
        kind=StageKind.INTERVIEW, is_active=True
    ).order_by("order").first()


def test_a_department_head_reaches_the_application_they_are_interviewing(
    office_boy_job, make_application, at_stage, staff
):
    """The Operations pipeline is not the Medical Director's department."""
    application = make_application(office_boy_job)
    stage = _first_interview_stage(office_boy_job)
    at_stage(application, stage.order, verified=True)

    # Booked directly: this is a test of who may REACH the row, and the
    # scheduling service quite rightly refuses an interviewer who does not
    # hold the stage's role. The state under test is "an interview exists
    # with me on it", however it came about.
    Interview.objects.create(
        application=application,
        stage=stage,
        interviewer=staff["medical_director"],
        scheduled_at=timezone.now() + dt.timedelta(days=2),
        duration_minutes=45,
        mode="video",
    )

    visible = pipeline_scope_filter(
        Application.objects.all(),
        staff["medical_director"].user,
        resource=Resource.APPLICATION,
        department_path="job_opening__department_id__in",
        assigned_path="interviews__interviewer_id",
        action=Action.VIEW,
    )
    assert application in visible


def test_that_head_still_sees_their_own_departments_pipeline(
    therapist_job, make_application, staff
):
    """Widening must not narrow either: the department rows are still there."""
    application = make_application(therapist_job)

    visible = pipeline_scope_filter(
        Application.objects.all(),
        staff["medical_director"].user,
        resource=Resource.APPLICATION,
        department_path="job_opening__department_id__in",
        assigned_path="interviews__interviewer_id",
        action=Action.VIEW,
    )
    assert application in visible


def test_an_unrelated_pipeline_stays_invisible(
    office_boy_job, make_application, staff
):
    """No interview, no department — still nothing."""
    application = make_application(office_boy_job)

    visible = pipeline_scope_filter(
        Application.objects.all(),
        staff["medical_director"].user,
        resource=Resource.APPLICATION,
        department_path="job_opening__department_id__in",
        assigned_path="interviews__interviewer_id",
        action=Action.VIEW,
    )
    assert application not in visible
