"""
Operator commands name the organization they act on.

These are the product's maintenance interface -- purge staging PII, roll leave
balances over, recompute a range of attendance, check the device integration --
and every one of them wrote or read organization-owned rows without ever saying
whose. That was invisible while the manager did not filter, and becomes an
`OrgContextMissing` the moment their app does.

It was invisible for a second reason too, which this file is here to end: no
test ran a management command. They were "the operator's interface, exercised by
hand", so the suite could be entirely green while `manage.py leave_rollover` was
broken for every deployment with more than one customer.

The rule each command inherits from `OrganizationCommand`: act on the named
organization, accept the only one when a deployment has exactly one, and REFUSE
to guess when there are several. Refusing matters more than it looks -- rolling
over the wrong company's balances, or recomputing a different company's employee
who happens to share an employee code, reports success either way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

#: (command name, extra kwargs). Each runs for real against a seeded
#: organization; the assertion is that it completes rather than raising
#: OrgContextMissing on its first query.
COMMANDS = [
    ("purge_import_staging", {}),
    ("leave_rollover", {"year": 2025}),
    (
        "recompute_attendance",
        {
            "date_from": str(dt.date(2025, 1, 6)),
            "date_to": str(dt.date(2025, 1, 7)),
        },
    ),
    ("essl_check", {}),
]


@pytest.mark.parametrize(
    ("command", "kwargs"), COMMANDS, ids=[name for name, _ in COMMANDS]
)
def test_a_command_runs_for_the_organization_it_is_given(command, kwargs, org_a):
    call_command(command, organization=org_a.slug, **kwargs)


@pytest.mark.parametrize(
    ("command", "kwargs"), COMMANDS, ids=[name for name, _ in COMMANDS]
)
def test_a_command_refuses_to_guess_between_organizations(
    command, kwargs, org_a, org_b
):
    """
    With two customers and no slug, stopping is the only safe answer.

    Picking the first would act on somebody chosen by primary-key order, and
    every one of these commands reports success either way.
    """
    with pytest.raises(CommandError) as refusal:
        call_command(command, **kwargs)

    assert "organization" in str(refusal.value).lower()


def test_a_command_accepts_the_only_organization_without_being_told(organization):
    """
    The single-company case, which must not need a flag.

    A self-hosted deployment has exactly one organization and should never have
    to name it. This is the same reason the base class does not simply demand
    the option.

    Deliberately takes the session organization rather than the `org_a`
    fixture: that fixture BUILDS a second company, so asking for the single-org
    behaviour while it exists is a contradiction -- which is how the first
    version of this test failed.
    """
    from apps.organization.models import Organization

    assert Organization.objects.count() == 1, (
        "this test is only meaningful while exactly one organization exists"
    )

    call_command("purge_import_staging")


def test_an_unknown_slug_is_refused_by_name(org_a):
    with pytest.raises(CommandError) as refusal:
        call_command("purge_import_staging", organization="no-such-company")

    assert "no-such-company" in str(refusal.value)
