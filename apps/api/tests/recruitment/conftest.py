"""Recruitment fixtures: both workflows, staffed with the roles they require."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from core.access.catalog import DepartmentKind
from tests.conftest import bind_membership

PASSWORD = "test-password-12345"


@pytest.fixture
def workflows(db, roles):
    from apps.workflows.seeds import seed_workflows

    built = seed_workflows()
    return {w.name: w for w in built}


@pytest.fixture
def staff(db, roles, org):
    """
    One employee per role the two workflows need.

    Built directly rather than through `create_employee`: these are the
    pre-existing organisation, and several are the very managers that
    `create_employee` would require to already exist.
    """
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    people: dict[str, Employee] = {}
    counter = [1000]

    def hire(role_code: str, department_kind: str, name: str) -> Employee:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@example.test", password=PASSWORD, first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)
        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:05d}",
            user=user,
            first_name=name,
            last_name=role_code.replace("_", " ").title(),
            department=org["departments"][department_kind],
            location=org["location"],
            date_of_joining=dt.date(2020, 1, 1),
        )
        people[role_code] = employee
        return employee

    hire("hr_head", DepartmentKind.HR, "Hema")
    hire("hr_manager", DepartmentKind.HR, "Hari")
    hire("recruiter", DepartmentKind.HR, "Ravi")

    hire("clinic_doctor", DepartmentKind.MEDICAL, "Chandni")
    hire("senior_doctor", DepartmentKind.MEDICAL, "Sanjay")
    hire("medical_director", DepartmentKind.MEDICAL, "Meera")

    hire("cre", DepartmentKind.OPERATIONS, "Chetan")
    hire("operations_manager", DepartmentKind.OPERATIONS, "Omkar")
    hire("operational_head", DepartmentKind.OPERATIONS, "Oindrila")

    hire("therapist", DepartmentKind.MEDICAL, "Tara")

    return people


@pytest.fixture
def admin_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="admin@example.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["admin"])
    bind_membership(user)
    return user


def _job(*, workflow, org, roles, staff, department_kind, role_code, title):
    from apps.recruitment.models import JobOpening, JobStatus

    return JobOpening.objects.create(
        title=title,
        workflow=workflow,
        department=org["departments"][department_kind],
        target_role=roles[role_code],
        location=org["location"],
        recruiter=staff["recruiter"],
        status=JobStatus.PUBLISHED,
        published_at=timezone.now(),
    )


@pytest.fixture
def therapist_job(workflows, org, roles, staff):
    return _job(
        workflow=workflows["Therapist hiring"],
        org=org, roles=roles, staff=staff,
        department_kind=DepartmentKind.MEDICAL,
        role_code="therapist",
        title="Therapist",
    )


@pytest.fixture
def office_boy_job(workflows, org, roles, staff):
    return _job(
        workflow=workflows["Office Boy hiring"],
        org=org, roles=roles, staff=staff,
        department_kind=DepartmentKind.OPERATIONS,
        role_code="office_boy",
        title="Office Boy",
    )


@pytest.fixture
def make_application(db):
    """Create a candidate and an application sitting at the workflow's first stage."""
    from apps.recruitment.models import Application, Candidate

    counter = [0]

    def _make(job, *, first_name="Asha", email=None):
        counter[0] += 1
        candidate = Candidate.objects.create(
            first_name=first_name,
            last_name="Candidate",
            email=email or f"candidate{counter[0]}@example.test",
            phone="9000000000",
            consent_given=True,
            consent_at=timezone.now(),
        )
        return Application.objects.create(
            candidate=candidate,
            job_opening=job,
            current_stage=job.workflow.first_stage,
        )

    return _make


@pytest.fixture
def at_stage(db):
    """Fast-forward an application to a stage by order, for focused tests."""

    def _move(application, order: int, *, verified: bool = True):
        stage = application.job_opening.workflow.stages.get(order=order)
        application.current_stage = stage
        application.is_verified = verified
        application.save(update_fields=["current_stage", "is_verified", "updated_at"])
        return application

    return _move


@pytest.fixture
def drive_to_selection(db, staff):
    """
    Run an application through its whole pipeline to SELECTED.

    Uses the real engine at every step rather than setting `status` directly,
    so tests that build on this inherit the genuine preconditions — an offer
    test cannot accidentally pass against a state the workflow could not
    actually produce.
    """
    from apps.recruitment.services.engine import record_decision
    from apps.workflows.models import Decision

    def _run(application, *, interview_roles, department_head):
        from .test_helpers import complete_interview_for  # local import, see below

        # The pipeline OPENS at Recruiter verification — there is no
        # "Application received" pass step any more.
        record_decision(
            application=application, actor=staff["recruiter"].user, decision=Decision.VERIFY
        )
        for order, role_code in zip((30, 40), interview_roles):
            complete_interview_for(application, order, role_code, staff)
            record_decision(
                application=application, actor=staff[role_code].user, decision=Decision.PASS
            )
        record_decision(
            application=application,
            actor=staff[department_head].user,
            decision=Decision.RECOMMEND_SELECT,
            rationale="Strong across every round; recommend proceeding.",
        )
        record_decision(
            application=application, actor=staff["hr_head"].user, decision=Decision.SELECT
        )
        application.refresh_from_db()
        return application

    return _run


@pytest.fixture
def completed_interview(db, staff):
    """Schedule an interview at a stage and submit passing feedback."""
    from apps.recruitment.services.interviews import schedule_interview, submit_feedback

    slot = [timezone.now() + dt.timedelta(days=1)]

    def _run(application, order: int, role_code: str, *, recommendation="hire"):
        stage = application.job_opening.workflow.stages.get(order=order)
        interviewer = staff[role_code]
        slot[0] = slot[0] + dt.timedelta(hours=3)

        interview = schedule_interview(
            application=application,
            stage=stage,
            interviewer=interviewer,
            actor=staff["hr_head"].user,
            scheduled_at=slot[0],
        )
        answers = {
            field.key: (3 if field.kind == "rating_1_5" else True if field.kind == "boolean" else "ok")
            for field in stage.feedback_form.fields.filter(is_required=True)
        }
        submit_feedback(
            interview=interview,
            actor=interviewer.user,
            answers=answers,
            recommendation=recommendation,
            strengths="Capable",
            concerns="None material",
            overall_rating=4,
        )
        return interview

    return _run
