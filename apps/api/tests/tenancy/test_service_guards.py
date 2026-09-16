"""
A service refuses an object that belongs to another organization.

The serializer scope closes the HTTP route by which one company's admin
offboarded another company's employee. It does not close the service itself:
`start_exit` checked only that the ACTOR held OFFBOARDING/CREATE, then acted on
whatever employee it was handed. A Celery task, a command or another service
reaches it with nothing in between.

These tests call the services directly, bypassing HTTP entirely, which is the
route the serializer fix cannot see.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.http import Http404

from core.access.guards import require_same_organization
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def _in_thirty_days():
    return dt.date.today() + dt.timedelta(days=30)


# ------------------------------------------------------------------ the guard


def test_an_object_from_the_bound_organization_passes(org_a):
    with acting_as(org_a.admin, organization=org_a.organization):
        require_same_organization(org_a.worker_employee, None)


def test_an_object_from_another_organization_is_refused_as_not_found(
    org_a, org_b, caplog
):
    """404, the same answer a nonexistent id gets -- never a 403 that confirms it."""
    import logging

    # `hrms.access` is configured with propagate=False, so caplog's handler on
    # the root logger never sees it. Attach the handler to that logger for the
    # duration instead of changing logging configuration for the sake of a test.
    access_logger = logging.getLogger("hrms.access")
    access_logger.addHandler(caplog.handler)
    try:
        with acting_as(org_a.admin, organization=org_a.organization):
            with pytest.raises(Http404):
                require_same_organization(
                    org_a.worker_employee, org_b.worker_employee
                )
    finally:
        access_logger.removeHandler(caplog.handler)

    assert "cross_tenant_object_refused" in caplog.text


def test_nothing_bound_is_a_refusal(org_a):
    with acting_as(None, organization=None):
        with pytest.raises(Http404):
            require_same_organization(org_a.worker_employee)


# ------------------------------------------------ the two exploited services


def test_start_exit_refuses_another_organizations_employee(org_a, org_b):
    from apps.employees.models import Employee
    from apps.offboarding.models import ExitWorkflow
    from apps.offboarding.services import start_exit

    victim = org_b.worker_employee
    before = Employee.objects.all_orgs().get(pk=victim.pk).status
    exit_type = ExitWorkflow._meta.get_field("exit_type").choices[0][0]

    with acting_as(org_a.admin, organization=org_a.organization):
        with pytest.raises(Http404):
            start_exit(
                employee=victim,
                actor=org_a.admin,
                exit_type=exit_type,
                last_working_date=_in_thirty_days(),
            )

    assert not ExitWorkflow.objects.all_orgs().filter(employee=victim).exists()
    assert Employee.objects.all_orgs().get(pk=victim.pk).status == before


def test_submit_resignation_refuses_another_organizations_employee(org_a, org_b):
    from apps.offboarding.models import ResignationRequest
    from apps.offboarding.services import submit_resignation

    victim = org_b.worker_employee
    existing = set(
        ResignationRequest.objects.all_orgs()
        .filter(employee=victim)
        .values_list("pk", flat=True)
    )

    with acting_as(org_a.admin, organization=org_a.organization):
        with pytest.raises(Http404):
            submit_resignation(
                employee=victim,
                actor=org_a.admin,
                requested_last_working_date=_in_thirty_days(),
                reason="Moving on",
            )

    assert not (
        ResignationRequest.objects.all_orgs()
        .filter(employee=victim)
        .exclude(pk__in=existing)
        .exists()
    )


def test_start_exit_still_works_for_the_bound_organization(org_a):
    """The positive control: the guard must not refuse the caller's own employee."""
    from apps.offboarding.models import ExitWorkflow
    from apps.offboarding.services import start_exit

    exit_type = ExitWorkflow._meta.get_field("exit_type").choices[0][0]
    with acting_as(org_a.admin, organization=org_a.organization):
        workflow = start_exit(
            employee=org_a.worker_employee,
            actor=org_a.admin,
            exit_type=exit_type,
            last_working_date=_in_thirty_days(),
        )

    assert workflow.organization_id == org_a.organization.pk
