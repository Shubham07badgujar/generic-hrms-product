"""
Provisioning an organization: all of it, or none of it.

The failure this suite exists to prevent is not "provisioning raised". It is
provisioning half-succeeding: an organization row that takes the slug so a
retry collides, roles but no administrator, or an administrator with no
membership -- who resolves to DENY_ALL and cannot use the system they were
created to run. None of those announce themselves. The operator sees success
and the customer sees a login that refuses them.

So the tests are mostly about what is true AFTER, and about what is true after
a failure partway through.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.platform.services.provisioning import (
    CONFIG_SEEDS,
    ProvisioningError,
    provision_organization,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def _provision(**overrides):
    kwargs = {
        "name": "Northwind Health",
        "slug": "northwind",
        "admin_email": "admin@northwind.example",
        "admin_first_name": "Asha",
    }
    kwargs.update(overrides)
    return provision_organization(**kwargs)


# ---------------------------------------------------------------------------
# What exists afterwards
# ---------------------------------------------------------------------------


def test_a_provisioned_organization_is_usable_by_its_administrator():
    """
    The whole point, stated as one assertion chain: the administrator can sign
    in, and resolves to their own organization with real authority.

    Membership is the part most easily forgotten and least visibly broken --
    tenant identity comes from it and nowhere else, so an admin without one
    holds their Admin role and still sees nothing at all.
    """
    from core.access.context import resolve_context

    result = _provision()

    assert result.organization.slug == "northwind"
    assert result.admin.email == "admin@northwind.example"
    assert result.admin.must_change_password, (
        "a provisioned administrator starts on a temporary password"
    )

    context = resolve_context(result.admin)
    assert context.organization_id == result.organization.pk
    assert context.grants, "the administrator resolved to no authority at all"


def test_the_organization_starts_in_pending_setup_and_can_still_work():
    """
    PENDING_SETUP is a WORKING state, not a locked one. The administrator is
    about to walk the setup wizard and has to be able to read and write to
    finish it; only finishing it makes the organization ACTIVE.
    """
    from apps.organization.models import OPERATIONAL_STATUSES, OrgStatus

    result = _provision()
    assert result.organization.status == OrgStatus.PENDING_SETUP
    assert OrgStatus.PENDING_SETUP in OPERATIONAL_STATUSES


def test_every_configuration_seed_ran_for_the_new_organization():
    """
    Not "the service returned a dict of seed names" -- the actual rows, scoped
    to the new organization, so a seed that silently wrote into somebody
    else's account or nowhere at all fails here.
    """
    from apps.accounts.models import Role
    from apps.attendance.models import ShiftRule
    from apps.employees.models import DocumentType
    from apps.leave.models import LeaveType
    from apps.offboarding.models import ClearanceTemplate
    from apps.onboarding.models import LetterTemplate
    from apps.workflows.models import HiringWorkflow

    result = _provision()
    org = result.organization

    for model in (
        Role, LeaveType, DocumentType, LetterTemplate,
        HiringWorkflow, ClearanceTemplate, ShiftRule,
    ):
        rows = model.objects.all_orgs().filter(organization=org).count()
        assert rows, f"{model._meta.label} got no rows for the new organization"

    assert set(result.seeded) == {key for key, _what, _fn in CONFIG_SEEDS}


def test_the_administrator_holds_their_own_organizations_admin_role():
    """
    `Role.code` is unique only PER ORGANIZATION now, so a lookup by code alone
    finds an arbitrary company's Admin role and grants it across the boundary.
    Provisioning scopes the lookup; this is what proves it stayed scoped.
    """
    from apps.accounts.models import UserRole

    first = _provision()
    second = _provision(
        name="Aperture Systems", slug="aperture",
        admin_email="admin@aperture.example",
    )

    # Across every organization on purpose: `.get()` then also proves the
    # administrator holds exactly ONE grant anywhere, not one per company.
    grant = UserRole.objects.all_orgs().get(user=second.admin)
    assert grant.role.organization_id == second.organization.pk
    assert grant.role.organization_id != first.organization.pk


def test_two_organizations_can_hold_the_same_business_keys():
    """Provisioning twice is not a special case; it is the normal case."""
    from apps.leave.models import LeaveType

    first = _provision()
    second = _provision(
        name="Aperture Systems", slug="aperture",
        admin_email="admin@aperture.example",
    )

    codes = {
        org.pk: set(
            LeaveType.objects.all_orgs()
            .filter(organization=org)
            .values_list("code", flat=True)
        )
        for org in (first.organization, second.organization)
    }
    assert codes[first.organization.pk] == codes[second.organization.pk]
    assert codes[first.organization.pk], "no leave types were seeded at all"


# ---------------------------------------------------------------------------
# What does not exist afterwards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"name": "  "}, "needs a name"),
        ({"slug": "", "name": "!!!"}, "usable slug"),
        ({"admin_email": ""}, "administrator's email"),
    ],
)
def test_bad_input_is_refused_before_anything_is_written(overrides, expected):
    from apps.organization.models import Organization

    before = Organization.objects.count()
    with pytest.raises(ProvisioningError, match=expected):
        _provision(**overrides)
    assert Organization.objects.count() == before


def test_a_taken_slug_is_refused_rather_than_colliding():
    from apps.organization.models import Organization

    _provision()
    before = Organization.objects.count()
    with pytest.raises(ProvisioningError, match="already taken"):
        _provision(admin_email="someone@else.example")
    assert Organization.objects.count() == before


def test_an_email_that_already_has_an_account_is_refused_with_the_reason():
    """
    The stated cost of the V1 identity model: `User.email` is the
    USERNAME_FIELD and is unique platform-wide, so one login belongs to one
    organization. That belongs in a clear message, not in a support ticket
    about a confusing 500.
    """
    _provision()
    with pytest.raises(ProvisioningError, match="belongs to exactly one organization"):
        _provision(slug="aperture", name="Aperture Systems")


def test_a_failure_partway_through_leaves_no_organization(monkeypatch):
    """
    The atomicity claim, tested by breaking a step in the MIDDLE -- after the
    organization row exists and after some seeds have run.

    Without the surrounding transaction this leaves a named organization with
    partial configuration and no administrator, holding the slug so the retry
    fails too.
    """
    import apps.platform.services.provisioning as provisioning
    from apps.organization.models import Organization

    def explode(organization):
        raise RuntimeError("seed failed")

    patched = list(provisioning.CONFIG_SEEDS)
    patched[2] = (patched[2][0], patched[2][1], explode)
    monkeypatch.setattr(provisioning, "CONFIG_SEEDS", patched)

    before = Organization.objects.count()
    with pytest.raises(RuntimeError, match="seed failed"):
        _provision()

    assert Organization.objects.count() == before
    assert not Organization.objects.filter(slug="northwind").exists()


def test_only_a_platform_admin_may_provision(org_a):
    """An organization's own Admin does not create organizations."""
    with pytest.raises(ProvisioningError, match="platform administrator"):
        _provision(actor=org_a.admin)


# ---------------------------------------------------------------------------
# The seed commands, which the tenancy conversion broke
# ---------------------------------------------------------------------------


def test_the_seed_commands_run_against_a_named_organization():
    """
    These raised `OrgContextMissing` on their first row after the tenancy
    conversion -- every one of them, on every run -- and nothing caught it,
    because no test drove a seed command. They are the operator's documented
    interface, so this drives them.
    """
    from apps.leave.models import LeaveType

    result = _provision()
    slug = result.organization.slug

    LeaveType.objects.all_orgs().filter(organization=result.organization).delete()

    for command in (
        "seed_leave", "seed_onboarding", "seed_workflows",
        "seed_offboarding", "seed_attendance",
    ):
        call_command(command, "--organization", slug, verbosity=0)

    assert LeaveType.objects.all_orgs().filter(
        organization=result.organization
    ).exists()


def test_a_seed_command_refuses_to_guess_between_organizations():
    """
    Picking the first would seed one customer's configuration into another's
    account, and `update_or_create` on a per-organization-unique code reports
    success either way -- so the wrong answer here is silent.
    """
    _provision()
    _provision(
        name="Aperture Systems", slug="aperture",
        admin_email="admin@aperture.example",
    )

    with pytest.raises(CommandError, match="Refusing to guess"):
        call_command("seed_leave", verbosity=0)


def test_the_seed_list_has_exactly_one_definition():
    """
    `seed_all` is a developer convenience command and provisioning is the
    customer path. While the list lived in `seed_all`, a real customer would
    have silently lacked whatever somebody added there and not here.
    """
    import apps.organization.management.commands.seed_all as seed_all

    assert seed_all.CONFIG_SEEDS is CONFIG_SEEDS


def test_statutory_rates_are_not_seeded_per_organization():
    """
    India's PF, ESI and Professional Tax tables are facts about the Republic
    of India, not about a customer. N copies would mean N certifications to
    verify and would defeat the four-eyes rule the statutory engine is built
    around.
    """
    assert not any(key == "statutory" for key, _what, _fn in CONFIG_SEEDS)
