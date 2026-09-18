"""
Scheduled work runs for one organization at a time, or it fails.

Celery serialises arguments, not context variables. A task that assumed it had
inherited a tenant would run under whatever the previous task on that worker
left behind — the same "correct only if something else ran first" shape as the
incident this whole design exists to prevent, on a worker instead of a request.

So every task that touches organization-owned data takes an `organization_id`,
re-reads it, and binds it. This file checks that per task rather than once,
because "scoped" and "happens to be called correctly" look identical from a
single passing example.

THE CASE THAT MATTERS is `test_a_task_handed_another_organizations_object_fails`.
A task bound to one tenant and handed another's object id must RAISE. Silently
finding nothing is indistinguishable from having no work to do, which is how a
customer stops being processed for a month with nothing in any log. It is also
why the tenant manager raises rather than returning an empty queryset — a
silently empty result would make this test pass while proving nothing.
"""

from __future__ import annotations

import pytest

from core.middleware import acting_as
from core.tasks import (
    UnknownOrganization,
    fan_out,
    organization_task,
    running_organizations,
)

pytestmark = pytest.mark.django_db


# ----------------------------------------------------------- the dispatcher


def test_the_dispatcher_reaches_every_running_organization(org_a, org_b):
    seen = []

    @organization_task(name="tests.record.for_organization")
    def record(organization):
        seen.append(organization.pk)
        return {"ok": True}

    summary = fan_out(record)

    assert org_a.organization.pk in seen
    assert org_b.organization.pk in seen
    assert summary["dispatched"] == len(seen)


def test_a_suspended_organization_is_not_dispatched_to(org_a, org_b):
    """
    Suspension destroys no work and changes no schedule.

    The customer is simply not queued for, and a restore resumes the schedule
    with no catch-up to arrange — which is the whole reason the fan-out is a
    query rather than a row per customer in the scheduler.
    """
    from apps.organization.models import OrgStatus

    # Suspending is a PLATFORM act, so it runs with no organization bound --
    # which is what makes its audit row org-less rather than stamped with
    # whichever tenant happened to be in force.
    with acting_as(None, organization=None):
        org_b.organization.status = OrgStatus.SUSPENDED
        org_b.organization.save(update_fields=["status"])

    running = {org.pk for org in running_organizations()}

    assert org_a.organization.pk in running
    assert org_b.organization.pk not in running


def test_a_subtask_for_a_suspended_organization_skips_rather_than_fails(org_a):
    """
    The race: suspended between dispatch and execution.

    That is not an error — nobody did anything wrong — so it is a skip with a
    reason rather than an exception that would retry forever.
    """
    from apps.organization.models import OrgStatus

    ran = []

    @organization_task(name="tests.skip_check.for_organization")
    def record(organization):
        ran.append(organization.pk)
        return {"ok": True}

    with acting_as(None, organization=None):
        org_a.organization.status = OrgStatus.SUSPENDED
        org_a.organization.save(update_fields=["status"])

    result = record(org_a.organization.pk)

    assert result == {"skipped": True, "reason": "not running"}
    assert ran == []


def test_an_unknown_organization_raises_rather_than_doing_nothing(db):
    """
    A queued id that no longer resolves is a fault, not an empty day's work.

    Returning quietly here would make a tenant stop being processed look
    exactly like a tenant with nothing to process.
    """
    import uuid

    @organization_task(name="tests.unknown.for_organization")
    def record(organization):  # pragma: no cover — must not be reached
        raise AssertionError("the body ran for an organization that does not exist")

    with pytest.raises(UnknownOrganization):
        record(uuid.uuid4())


# --------------------------------------------------- binding and isolation


def test_a_subtask_binds_its_own_organization_whatever_was_bound_before(org_a, org_b):
    """
    The inheritance question, asked directly.

    The task is CALLED while the other organization is bound, which is the
    worker's real condition: the previous job on that process left its tenant
    behind. The argument must win.
    """
    from core.middleware import get_current_org_id

    bound = []

    @organization_task(name="tests.binding.for_organization")
    def record(organization):
        bound.append(get_current_org_id())
        return {"ok": True}

    with acting_as(org_b.admin, organization=org_b.organization):
        record(org_a.organization.pk)

    assert bound == [org_a.organization.pk]


def test_a_subtask_sees_only_its_own_organizations_rows(org_a, org_b):
    """
    The first two lines of the per-task matrix, on a real queryset.

    Nothing in the task body says "organization=": the fail-closed manager
    reads the bound tenant, which is exactly why the conversion is a wrapper
    rather than a rewrite of every service the tasks call.
    """
    from apps.employees.models import Employee

    seen = {}

    @organization_task(name="tests.rows.for_organization")
    def record(organization):
        seen[organization.pk] = {e.pk for e in Employee.objects.all()}
        return {"ok": True}

    record(org_a.organization.pk)
    record(org_b.organization.pk)

    a_rows, b_rows = seen[org_a.organization.pk], seen[org_b.organization.pk]
    assert a_rows and b_rows, "both organizations need employees for this to prove anything"
    assert not (a_rows & b_rows)
    assert org_a.hr_employee.pk in a_rows
    assert org_a.hr_employee.pk not in b_rows


def test_a_task_handed_another_organizations_object_fails(org_a, org_b):
    """
    THE CASE THE PLAN INSISTS ON, and the one most likely to be skipped.

    A task bound to one tenant, handed the other's object id, must raise. The
    difference between a task that is SCOPED and one that merely HAPPENS to be
    called correctly is entirely visible here and nowhere else.
    """
    from apps.recruitment.models import JobOpening
    from apps.recruitment.tasks import sync_google_form_responses_for_job

    other_job = org_b.rows["job_opening"]

    with pytest.raises(JobOpening.DoesNotExist):
        sync_google_form_responses_for_job(org_a.organization.pk, other_job.pk)


def test_the_same_task_succeeds_with_its_own_object(org_a, monkeypatch):
    """
    The positive control for the case above.

    Without it, the refusal would also be satisfied by a task that had simply
    stopped working — and a broken task refusing everything looks exactly like
    a correctly scoped one from the outside.
    """
    from apps.recruitment import tasks as recruitment_tasks

    called = {}

    def fake_sync(job, *, provider):
        called["job"] = job.pk
        return {"seen": 1, "created": 0, "duplicate": 1, "refused": 0}

    monkeypatch.setattr(
        "apps.recruitment.services.external_forms.sync_responses", fake_sync
    )
    monkeypatch.setattr(
        "apps.recruitment.services.external_forms.provider_for_job", lambda: object()
    )

    own_job = org_a.rows["job_opening"]
    result = recruitment_tasks.sync_google_form_responses_for_job(
        org_a.organization.pk, own_job.pk
    )

    assert called["job"] == own_job.pk
    assert result["duplicate"] == 1


# ------------------------------------------------------- the real task set


#: Every per-organization subtask the product ships, and how to call it.
#: Parametrised so a new one is covered the day it is added rather than the day
#: someone remembers — the same reason the route walker derives its routes from
#: the resolver.
REAL_SUBTASKS = [
    ("apps.attendance.tasks", "sync_essl_for_organization", {}),
    ("apps.leave.tasks", "monthly_accrual_for_organization", {}),
    ("apps.leave.tasks", "convert_short_leave_for_organization", {}),
    ("apps.leave.tasks", "flag_absences_for_organization", {}),
    ("apps.imports.tasks", "purge_staging_pii_for_organization", {"apply": False}),
    ("apps.reporting.tasks", "refresh_snapshots_for_organization", {}),
    (
        "apps.recruitment.tasks",
        "purge_expired_candidates_for_organization",
        {"apply": False},
    ),
    (
        "apps.recruitment.tasks",
        "sync_google_form_responses_for_organization",
        {},
    ),
]


def _load(module_name, attribute):
    import importlib

    return getattr(importlib.import_module(module_name), attribute)


@pytest.mark.parametrize(
    ("module_name", "attribute", "kwargs"),
    REAL_SUBTASKS,
    ids=[f"{m.split('.')[1]}.{a}" for m, a, _ in REAL_SUBTASKS],
)
def test_every_shipped_subtask_runs_for_one_organization_without_touching_the_other(
    module_name, attribute, kwargs, org_a, org_b
):
    """
    Per task, not once. A wrapper applied to six tasks and forgotten on the
    seventh is the ordinary way this kind of conversion goes wrong.
    """
    from apps.employees.models import Employee
    from apps.recruitment.models import Candidate

    task = _load(module_name, attribute)

    before_b = {
        "employees": sorted(
            Employee.objects.all_orgs()
            .filter(organization=org_b.organization)
            .values_list("pk", "updated_at")
        ),
        "candidates": sorted(
            Candidate.objects.all_orgs()
            .filter(organization=org_b.organization)
            .values_list("pk", "first_name")
        ),
    }

    task(org_a.organization.pk, **kwargs)

    after_b = {
        "employees": sorted(
            Employee.objects.all_orgs()
            .filter(organization=org_b.organization)
            .values_list("pk", "updated_at")
        ),
        "candidates": sorted(
            Candidate.objects.all_orgs()
            .filter(organization=org_b.organization)
            .values_list("pk", "first_name")
        ),
    }

    assert after_b == before_b, (
        f"{attribute} ran for {org_a.slug} and changed rows in {org_b.slug}"
    )


@pytest.mark.parametrize(
    ("module_name", "attribute", "kwargs"),
    REAL_SUBTASKS,
    ids=[f"{m.split('.')[1]}.{a}" for m, a, _ in REAL_SUBTASKS],
)
def test_every_shipped_subtask_refuses_an_organization_that_does_not_exist(
    module_name, attribute, kwargs, db
):
    """One wrapper, applied everywhere — asserted everywhere rather than assumed."""
    import uuid

    task = _load(module_name, attribute)

    with pytest.raises(UnknownOrganization):
        task(uuid.uuid4(), **kwargs)


def test_the_subtask_list_covers_everything_the_codebase_declares():
    """
    Guards the guard.

    The parametrised lists above are only as good as their coverage, and a
    subtask added without a line here would be silently untested.
    """
    import re
    from pathlib import Path

    from core.tasks import PER_ORGANIZATION_SUFFIXES

    root = Path(__file__).resolve().parents[2]
    declares = re.compile(r'@organization_task\(\s*name="([^"]+)"')

    declared = set()
    for path in (root / "apps").rglob("tasks.py"):
        declared |= set(declares.findall(path.read_text(encoding="utf-8")))

    assert declared, "the scan found no per-organization tasks at all"

    # `.for_job` subtasks are exercised by name further up, not by the matrix:
    # they take an object id and so cannot be called uniformly.
    per_organization = {
        name for name in declared if name.endswith(PER_ORGANIZATION_SUFFIXES[0])
    }

    assert len(REAL_SUBTASKS) == len(per_organization), (
        f"{len(per_organization)} per-organization subtasks are declared and "
        f"{len(REAL_SUBTASKS)} are exercised. Declared: "
        f"{sorted(per_organization)}"
    )
