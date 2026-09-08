"""
Object-level authorization for the hiring pipeline.

`PipelineScopedMixin` has always narrowed the LIST of recruitment rows. What was
missing was the matching check on a single object supplied by id: a caller could
name a `job_opening` in a POST body and nothing asked whether it was theirs.

That was unexploitable only because no role below `Scope.ALL` holds
`APPLICATION/CREATE`. These tests refuse to rest on that reasoning — they grant a
department-scoped principal the create right explicitly and assert the check
holds on its own, so the protection survives a future matrix change rather than
depending on one.
"""

from __future__ import annotations

import pytest
from django.http import Http404

from apps.recruitment.access import pipeline_scope_filter, resolve_job_opening
from core.access import Action, Resource, Scope

pytestmark = pytest.mark.django_db


@pytest.fixture
def pipeline(db, org, roles, everyone):
    """
    One published workflow per function, and a job opening in each department.

    Built here rather than imported from the recruitment suite: that suite
    stands up its own organisation, and two org fixtures in one test would each
    claim the same department codes.
    """
    from apps.recruitment.models import JobOpening
    from apps.workflows.seeds import seed_workflows
    from core.access.catalog import DepartmentKind

    workflows = {w.department_kind: w for w in seed_workflows()}
    jobs = {}
    for kind, title, role_code in (
        (DepartmentKind.MEDICAL, "Therapist", "therapist"),
        (DepartmentKind.OPERATIONS, "Office Boy", "office_boy"),
    ):
        jobs[kind] = JobOpening.objects.create(
            title=title,
            workflow=workflows[kind],
            department=org["departments"][kind],
            target_role=roles[role_code],
            status="published",
        )
    return jobs


@pytest.fixture
def medical_job(pipeline):
    from core.access.catalog import DepartmentKind

    return pipeline[DepartmentKind.MEDICAL]


@pytest.fixture
def operations_job(pipeline):
    from core.access.catalog import DepartmentKind

    return pipeline[DepartmentKind.OPERATIONS]


@pytest.fixture
def grant(everyone):
    """Give one principal one extra grant, without touching the shipped matrix."""
    from apps.accounts.models import UserPermissionOverride
    from core.access.context import invalidate

    def _grant(user, resource, action, scope):
        UserPermissionOverride.objects.create(
            user=user,
            resource=resource,
            action=action,
            scope=scope,
            reason="Pinning object-level authorization in a test.",
            granted_by=everyone["admin"],
        )
        invalidate(user.pk)

    return _grant


# ------------------------------------------------------ the resolver itself


def test_all_scope_reaches_any_job(everyone, medical_job):
    assert resolve_job_opening(medical_job, user=everyone["hr_head"]).pk == medical_job.pk


def test_a_department_head_reaches_their_own_departments_job(everyone, medical_job):
    """Medical Director holds JOB_OPENING/VIEW at DEPARTMENT over Medical."""
    resolved = resolve_job_opening(medical_job, user=everyone["medical_director"])

    assert resolved.pk == medical_job.pk


def test_a_department_head_cannot_reach_another_departments_job(
    everyone, operations_job
):
    """
    The isolation claim. Operations' job is invisible to Medical's head.

    404 rather than 403 on purpose — a 403 confirms the job exists, which is an
    enumeration oracle over another department's hiring.
    """
    with pytest.raises(Http404):
        resolve_job_opening(operations_job, user=everyone["medical_director"])


def test_a_principal_with_no_job_grant_reaches_nothing(everyone, medical_job):
    with pytest.raises(Http404):
        resolve_job_opening(medical_job, user=everyone["office_boy"])


# ------------------------------------------------------------ over the API


def test_creating_an_application_checks_the_job_not_just_the_workflow(
    auth, everyone, operations_job, grant
):
    """
    The gap itself, at the API.

    The Medical Director is given APPLICATION/CREATE at DEPARTMENT — a grant
    nobody holds today — and then attaches a candidate to an OPERATIONS job.
    Before the fix the serializer checked only that the workflow was published,
    so this succeeded.
    """
    from apps.recruitment.models import Candidate

    director = everyone["medical_director"]
    grant(director, Resource.APPLICATION, Action.CREATE, Scope.DEPARTMENT)
    candidate = Candidate.objects.create(
        first_name="Test", last_name="Person", email="pipeline-a@example.test",
        consent_given=True,
    )

    response = auth(director).post(
        "/api/v1/applications/",
        {"candidate": str(candidate.pk), "job_opening": str(operations_job.pk)},
    )

    assert response.status_code == 404, (
        f"A department-scoped principal attached a candidate to another "
        f"department's job opening (got {response.status_code})."
    )


def test_the_same_principal_can_still_use_their_own_departments_job(
    auth, everyone, medical_job, grant
):
    """The check must admit the legitimate case, or it is just an outage."""
    from apps.recruitment.models import Candidate

    director = everyone["medical_director"]
    grant(director, Resource.APPLICATION, Action.CREATE, Scope.DEPARTMENT)
    candidate = Candidate.objects.create(
        first_name="Test", last_name="Person", email="pipeline-b@example.test",
        consent_given=True,
    )

    response = auth(director).post(
        "/api/v1/applications/",
        {"candidate": str(candidate.pk), "job_opening": str(medical_job.pk)},
    )

    assert response.status_code == 201, response.data


# ------------------------------------------------- list and object agreement


def test_the_list_filter_and_the_object_check_agree(everyone, operations_job):
    """
    Both now come from `pipeline_scope_filter`. When they were two
    implementations the object check was simply absent, so pin them together.
    """
    from apps.recruitment.models import JobOpening

    director = everyone["medical_director"]
    visible = pipeline_scope_filter(
        JobOpening.objects.all(),
        director,
        resource=Resource.JOB_OPENING,
        department_path="department_id__in",
        assigned_path="recruiter_id",
    )

    assert operations_job.pk not in set(visible.values_list("pk", flat=True))
    with pytest.raises(Http404):
        resolve_job_opening(operations_job, user=director)
