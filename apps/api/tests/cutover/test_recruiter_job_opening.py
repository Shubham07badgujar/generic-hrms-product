"""
Cutover gate: a Recruiter can create a job opening without help.

The Recruiter held CREATE on JOB_OPENING but no read on the reference lists
that opening points at — department, workflow, target role, designation,
location, seniority band. The grant existed and could not be used: every
required dropdown answered 403, so the form never reached a saveable state and
only HR Head or Admin could actually open a role.

Widening a read grant is exactly where an unintended write can ride along, so
this file asserts the capability and its limit together: the Recruiter can now
fill in and submit the whole form, and still cannot author any of the
organisation's own structure.
"""

from __future__ import annotations

import pytest

from core.access import Action, Resource, Scope, can
from core.access.catalog import WRITE_ACTIONS

pytestmark = pytest.mark.django_db

DEPARTMENTS = "/api/v1/departments/"
DESIGNATIONS = "/api/v1/designations/"
LOCATIONS = "/api/v1/locations/"
LEVELS = "/api/v1/levels/"
ROLES = "/api/v1/roles/"
WORKFLOWS = "/api/v1/workflows/"
JOBS = "/api/v1/jobs/"

#: Every list the job opening form loads, and whether the field is required.
FORM_SOURCES = [
    (DEPARTMENTS, "Department", True),
    (WORKFLOWS, "Hiring workflow", True),
    (ROLES, "Role on hire", True),
    (DESIGNATIONS, "Designation", False),
    (LOCATIONS, "Location", False),
    (LEVELS, "Seniority level", False),
]

#: The organisation's own configuration. Readable for pickers, never writable.
ORG_CONFIG = [
    (Resource.DEPARTMENT, DEPARTMENTS),
    (Resource.DESIGNATION, DESIGNATIONS),
    (Resource.LOCATION, LOCATIONS),
    (Resource.ROLE, ROLES),
    (Resource.HIRING_WORKFLOW, WORKFLOWS),
]


@pytest.fixture
def seeded_workflows(db, roles):
    from apps.workflows.seeds import seed_workflows

    return {w.name: w for w in seed_workflows()}


@pytest.fixture
def recruiter_api(auth, everyone):
    return auth(everyone["recruiter"])


def _compatible_pair(seeded_workflows):
    """
    A published workflow and a department it may legitimately run in.

    Chosen workflow-first. Only some functions have a seeded pipeline, so
    picking a department first and hunting for a workflow finds nothing the
    moment the arbitrary "first" department is Finance.
    """
    from apps.organization.models import Department

    for workflow in seeded_workflows.values():
        if not workflow.is_published:
            continue
        department = (
            Department.objects.filter(kind=workflow.department_kind).first()
            if workflow.department_kind
            else Department.objects.first()
        )
        if department:
            return workflow, department
    raise AssertionError("No published workflow has a department to run in.")


# ------------------------------------------------------------- the grant


@pytest.mark.parametrize(
    "resource",
    [
        Resource.DEPARTMENT,
        Resource.DESIGNATION,  # also serves /levels/
        Resource.LOCATION,
        Resource.ROLE,
        Resource.HIRING_WORKFLOW,
    ],
)
def test_recruiter_reads_every_reference_list_the_form_needs(everyone, resource):
    """Resolved through `can()` — the same call the viewset makes."""
    assert can(everyone["recruiter"], resource, Action.VIEW) == Scope.ALL


def test_recruiter_can_view_create_and_edit_job_openings(everyone):
    recruiter = everyone["recruiter"]

    assert can(recruiter, Resource.JOB_OPENING, Action.VIEW) == Scope.ALL
    assert can(recruiter, Resource.JOB_OPENING, Action.CREATE) == Scope.ALL
    assert can(recruiter, Resource.JOB_OPENING, Action.EDIT) == Scope.ALL


# ------------------------------------------------------------- the limit


@pytest.mark.parametrize("resource,_url", ORG_CONFIG)
def test_recruiter_holds_no_write_on_organisation_configuration(
    everyone, resource, _url
):
    """
    The whole risk of this change, stated as an assertion.

    Reading a department to choose one is not authoring departments. Every
    write action is checked, not just create — an EDIT on Role would be a
    privilege-escalation path, and a DELETE on Department would take job
    openings with it.
    """
    recruiter = everyone["recruiter"]

    granted = {
        action
        for action in WRITE_ACTIONS
        if can(recruiter, resource, action) > Scope.NONE
    }
    assert not granted, (
        f"Recruiter gained write actions {sorted(granted)} on '{resource}' — "
        f"reading these lists must not confer authoring them."
    )


def test_recruiter_still_cannot_manage_users_or_assign_roles(everyone):
    """
    ROLE/VIEW was the grant most likely to over-reach, so its ceiling is
    asserted explicitly.

    Being able to name the role a candidate will be hired into must not become
    being able to hand that role to anybody.
    """
    recruiter = everyone["recruiter"]

    for action in WRITE_ACTIONS:
        assert can(recruiter, Resource.ROLE, action) == Scope.NONE
        assert can(recruiter, Resource.USER, action) == Scope.NONE

    assert can(recruiter, Resource.USER, Action.VIEW) == Scope.NONE
    assert can(recruiter, Resource.ORG_SETTINGS, Action.EDIT) == Scope.NONE


def test_recruiter_cannot_delete_a_job_opening(everyone):
    """Create and edit, but not destroy — applications hang off these rows."""
    assert can(everyone["recruiter"], Resource.JOB_OPENING, Action.DELETE) == Scope.NONE


# ------------------------------------------------------- over HTTP: reads


@pytest.mark.parametrize("url,field,required", FORM_SOURCES)
def test_every_dropdown_loads_for_a_recruiter(recruiter_api, org, url, field, required):
    """
    The regression this change exists to fix.

    Each of these answered 403 before, and three of them are required fields,
    so the form could not be completed at all.
    """
    response = recruiter_api.get(url)

    assert response.status_code == 200, (
        f"'{field}' returned {response.status_code}; the job opening form "
        f"cannot be {'completed' if required else 'fully filled'} without it."
    )


def test_the_role_picker_offers_hireable_roles_and_not_the_system_principals(
    recruiter_api, roles
):
    """
    Role on hire still respects the hierarchy rules.

    The picker is narrowed to roles that are grantable in-app AND require an
    employee record, which is what keeps CEO and Admin out of it — they are
    `requires_employee=False` principals, and neither is something a candidate
    can be hired into.
    """
    payload = recruiter_api.get(ROLES).data
    catalogue = payload["results"] if isinstance(payload, dict) else payload

    pickable = {
        r["code"] for r in catalogue if r["is_grantable"] and r["requires_employee"]
    }

    assert "ceo" not in pickable
    assert "admin" not in pickable
    assert "therapist" in pickable, "A real hireable role should be offered."


# ------------------------------------------------------ over HTTP: writes


@pytest.mark.parametrize("resource,url", ORG_CONFIG)
def test_a_recruiter_cannot_write_organisation_configuration_over_http(
    recruiter_api, resource, url
):
    """
    405, not 403 — and that is the stronger result.

    These endpoints are read-only viewsets for EVERY role including Admin, so
    the write methods are not routed at all. The permission check never has to
    hold the line because there is no surface to attack; the grant-level
    assertion above covers the authorisation half.
    """
    for method, body in (
        (recruiter_api.post, {"name": "Invented"}),
        (recruiter_api.patch, {"name": "Renamed"}),
        (recruiter_api.delete, None),
    ):
        target = url if method is recruiter_api.post else f"{url}some-id/"
        response = method(target, body) if body is not None else method(target)

        assert response.status_code in (403, 404, 405), (
            f"{resource} accepted a write from a recruiter: {response.status_code}"
        )
        assert response.status_code != 201, f"{resource} was created by a recruiter."


# ------------------------------------------------- the form, end to end


def test_recruiter_creates_a_job_opening_from_the_reference_data_it_can_read(
    recruiter_api, org, seeded_workflows, roles
):
    """
    The whole point, exercised the way the SPA does it: load each list, pick
    from it, submit — with nothing but the recruiter's own permissions.
    """
    workflow, department = _compatible_pair(seeded_workflows)

    # Read every list through the recruiter's own client — if any of these
    # 403s, the chained lookups below fail and the form was never fillable.
    departments = recruiter_api.get(DEPARTMENTS).data
    designation = recruiter_api.get(DESIGNATIONS).data[0]
    location = recruiter_api.get(LOCATIONS).data[0]
    level = recruiter_api.get(LEVELS).data[0]

    assert str(department.pk) in {str(d["id"]) for d in departments}, (
        "The department the recruiter must choose is not in the list it can read."
    )

    catalogue = recruiter_api.get(ROLES).data
    target_role = next(
        r for r in catalogue if r["is_grantable"] and r["requires_employee"]
    )

    created = recruiter_api.post(
        JOBS,
        {
            "title": "Senior Physiotherapist",
            "department": str(department.pk),
            "workflow": str(workflow.pk),
            "target_role": target_role["id"],
            "designation": designation["id"],
            "location": location["id"],
            "level": level["id"],
            "openings_count": 2,
            "description": "Runs the afternoon clinic.",
            "requirements": "Five years, BPT.",
        },
    )

    assert created.status_code == 201, created.data
    assert created.data["status"] == "draft", "A new opening must start as a draft."
    assert created.data["openings_count"] == 2

    job_id = created.data["id"]

    edited = recruiter_api.patch(f"{JOBS}{job_id}/", {"openings_count": 3})
    assert edited.status_code == 200
    assert edited.data["openings_count"] == 3

    published = recruiter_api.post(f"{JOBS}{job_id}/publish/")
    assert published.status_code == 200
    assert published.data["status"] == "published"
    assert published.data["published_at"], "Publishing must stamp the time."


def test_a_draft_workflow_is_still_refused(recruiter_api, org, seeded_workflows, roles):
    """
    Rule 4, half one. The reference lists opened up; the validation behind them
    did not. A draft pipeline would move candidates through stages nobody has
    agreed to yet.
    """
    workflow, department = _compatible_pair(seeded_workflows)
    workflow.is_published = False
    workflow.save(update_fields=["is_published"])

    catalogue = recruiter_api.get(ROLES).data
    target_role = next(
        r for r in catalogue if r["is_grantable"] and r["requires_employee"]
    )

    response = recruiter_api.post(
        JOBS,
        {
            "title": "Too early",
            "department": str(department.pk),
            "workflow": str(workflow.pk),
            "target_role": target_role["id"],
        },
    )

    assert response.status_code == 400
    assert "draft" in str(response.data).lower()


def test_a_workflow_from_another_function_is_still_refused(
    recruiter_api, org, seeded_workflows, roles
):
    """
    Rule 4, half two: department and workflow functions must agree.

    The SPA filters the dropdown so this combination is not offered, but the
    server is what makes it true — the recruiter now reaches both lists, and a
    hand-made request must still be refused.
    """
    from apps.organization.models import Department

    typed = [w for w in seeded_workflows.values() if w.department_kind]
    if not typed:
        pytest.skip("No function-specific workflow seeded to test against.")
    workflow = typed[0]

    mismatched = (
        Department.objects.exclude(kind=workflow.department_kind).first()
    )
    assert mismatched, "Need a department of a different function."

    catalogue = recruiter_api.get(ROLES).data
    target_role = next(
        r for r in catalogue if r["is_grantable"] and r["requires_employee"]
    )

    response = recruiter_api.post(
        JOBS,
        {
            "title": "Wrong function",
            "department": str(mismatched.pk),
            "workflow": str(workflow.pk),
            "target_role": target_role["id"],
        },
    )

    assert response.status_code == 400
    assert "function" in str(response.data).lower()


# ------------------------------------------------------- nobody else moved


@pytest.mark.parametrize(
    "role_code", ["employee", "office_boy", "therapist", "payroll_executive"]
)
def test_the_new_reads_did_not_leak_to_unrelated_roles(everyone, role_code):
    """
    The grants were added to specific roles. A helper edited in the wrong
    place would hand the whole organisation chart to everybody, so this
    checks the blast radius rather than assuming it.

    The CRE left this list deliberately: interviewer-block roles now read
    LOCATION and DESIGNATION (the reference lists a job posting points at),
    which the interviewer test below pins. DEPARTMENT and ROLE stay closed
    to them.
    """
    user = everyone[role_code]

    for resource in (Resource.DEPARTMENT, Resource.ROLE, Resource.LOCATION):
        assert can(user, resource, Action.VIEW) == Scope.NONE, (
            f"'{role_code}' unexpectedly gained {resource}.view."
        )


def test_interviewer_reads_reference_lists_but_not_org_structure(everyone):
    """An interviewer reads location/designation names; never DEPARTMENT/ROLE."""
    user = everyone["cre"]
    assert can(user, Resource.LOCATION, Action.VIEW) == Scope.ALL
    assert can(user, Resource.DESIGNATION, Action.VIEW) == Scope.ALL
    assert can(user, Resource.JOB_OPENING, Action.VIEW) == Scope.SELF
    for resource in (Resource.DEPARTMENT, Resource.ROLE):
        assert can(user, resource, Action.VIEW) == Scope.NONE
    for action in (Action.CREATE, Action.EDIT, Action.DELETE):
        assert can(user, Resource.LOCATION, action) == Scope.NONE
        assert can(user, Resource.JOB_OPENING, action) == Scope.NONE
