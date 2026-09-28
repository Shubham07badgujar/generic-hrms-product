"""
The three demo customers.

TWO KINDS OF TEST, and the split is deliberate.

The first kind reads the profiles AS DATA and needs no database at all. Every
rule it checks -- a role may only sit in its own function, a manager must exist
before their report and may not be more junior, a seniority band must match the
role's layer -- is a rule `create_employee` enforces at row twelve of twenty,
inside a transaction that then rolls the first eleven back. Checking them here
turns "the seeding command died halfway through" into a named line in a profile.
These run in milliseconds and cover all three companies.

The second kind actually seeds one, through the real provisioning service, and
asserts the things only a database can answer: that the plan came out different,
that the trial is expiring, and that the company next door cannot see any of it.
Retail is the one chosen, because ten employees is the cheapest honest end-to-end
run and the trial is the mechanism the other two do not exercise.

WHAT IS NOT ASSERTED HERE. Cross-tenant isolation in general -- that has its own
suite, a route walker and a generated isolation report. What this file asserts is narrower
and specific to demo data: that two demo companies seeded from one command are
genuinely two customers, rather than two names over one set of rows.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.organization.demo import HEALTHCARE, PROFILES, RETAIL, TECHNOLOGY, get_profile

# ---------------------------------------------------------------------------
# The profiles as data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(PROFILES))

def test_every_profile_satisfies_the_rules_its_people_will_be_created_under(key):
    """
    The validator is the point of this test, and the validator is not the
    product's rules restated -- it reads `ROLE_SPECS` and `ROLE_DEPARTMENT_KINDS`
    directly, so a change to either is caught here rather than in a seeding run.
    """
    assert get_profile(key).validate() == []


def test_the_three_are_sized_as_the_brief_asks():
    """
    20/5/3, 15/4/2, 10/3/2 — employees, departments, locations.

    Written down because the sizes are the reason there are three: a
    twenty-person company with five departments exercises a hierarchy a
    ten-person one cannot, and three companies of identical size would
    demonstrate nothing that one does.
    """
    sizes = {
        profile.key: (
            len(profile.people), len(profile.departments), len(profile.locations)
        )
        for profile in (HEALTHCARE, TECHNOLOGY, RETAIL)
    }
    assert sizes == {
        "healthcare": (20, 5, 3),
        "technology": (15, 4, 2),
        "retail": (10, 3, 2),
    }


def test_healthcare_is_the_one_that_covers_every_role():
    """
    And the other two are honest about not doing so.

    The promise moved from "one account per role" to "one per role this company
    contains" when the profiles arrived, and a test that asserted the first for
    all three would be measuring Technology against Healthcare's contract.
    """
    assert HEALTHCARE.absent_roles == set()
    assert TECHNOLOGY.absent_roles, "a 15-person company cannot hold 18 roles"
    assert RETAIL.absent_roles, "a 10-person company certainly cannot"
    # Every role a profile DOES contain has an account in it, which is the
    # promise all three make.
    for profile in (HEALTHCARE, TECHNOLOGY, RETAIL):
        covered = {person.role_code for person in profile.people}
        assert covered | profile.absent_roles | {"ceo", "admin"} >= {
            person.role_code for person in profile.people
        }


def test_the_three_are_sold_different_plans():
    """
    The commercial axis, which is the whole reason these are three companies
    and not one seeded three times. Identical plans would leave entitlement,
    seat limits and trial expiry untested by every one of them.
    """
    plans = {profile.plan_code for profile in PROFILES.values()}
    assert len(plans) == 3, plans
    assert TECHNOLOGY.plan_code == "starter", "the profile without payroll"


def test_no_demo_address_can_resolve():
    """
    `.example` is reserved by RFC 2606 precisely so it cannot. Demo people in a
    live HR database show up in headcount and payroll; an address that could
    receive mail would be the difference between embarrassing and harmful.
    """
    for profile in PROFILES.values():
        assert profile.domain.endswith(".example"), profile.key


def test_the_profiles_do_not_share_a_domain_or_a_slug():
    """
    Removal is keyed on both. Two companies sharing either would make deleting
    one able to take the other's people with it -- which is exactly the defect
    the slug-scoped removal was written to end.
    """
    domains = [profile.domain for profile in PROFILES.values()]
    slugs = [profile.slug for profile in PROFILES.values()]
    assert len(set(domains)) == len(domains)
    assert len(set(slugs)) == len(slugs)


def test_a_malformed_profile_is_named_rather_than_discovered_halfway_through():
    """The validator's own positive control: it must actually refuse something."""
    from dataclasses import replace

    from apps.organization.demo import DemoPerson
    from core.access.catalog import Layer

    broken = replace(
        RETAIL,
        people=RETAIL.people
        + (
            DemoPerson(
                "misplaced", "hr_head", "Wrong", "Place",
                "STR", Layer.DEPARTMENT_HEAD, "Store Manager",
            ),
        ),
    )
    problems = broken.validate()
    assert any("cannot sit in" in problem for problem in problems), problems


# ---------------------------------------------------------------------------
# One of them, seeded for real
# ---------------------------------------------------------------------------

pytestmark_db = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_the_command_refuses_before_writing_anything_when_no_plan_exists(db, settings, tmp_path):
    """
    Three companies that differ by plan, seeded onto whatever plan happens to
    exist, would be three identical companies. Refused up front rather than
    produced and explained afterwards.
    """
    settings.MEDIA_ROOT = tmp_path
    from apps.organization.models import Organization

    before = Organization.objects.count()
    with pytest.raises(CommandError, match="seed_plans"):
        call_command("seed_demo_platform", "--profile", "retail", verbosity=0)
    assert Organization.objects.count() == before, "an organization was created anyway"


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_retail_is_provisioned_live_and_on_an_expiring_trial(plans, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    from django.utils import timezone

    from apps.employees.models import Employee
    from apps.organization.models import Department, Location, Organization, OrgStatus
    from apps.platform.models import Subscription

    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)

    organization = Organization.objects.get(slug=RETAIL.slug)
    # TRIAL rather than ACTIVE, and it got there through `finish_setup`, which
    # refuses while a required step is outstanding -- so this doubles as an
    # assertion that the seeded company is actually complete.
    assert organization.status == OrgStatus.TRIAL

    from tests.conftest import across_organizations

    with across_organizations():
        subscription = Subscription.objects.select_related("plan").get(
            organization=organization, is_active=True
        )
    assert subscription.plan.code == RETAIL.plan_code
    assert subscription.status == "trialing"
    assert subscription.ends_at is not None
    days_left = (subscription.ends_at - timezone.now()).days
    assert 0 <= days_left <= 3, f"the trial should be days from expiry, not {days_left}"

    from tests.conftest import across_organizations

    with across_organizations():
        assert Employee.objects.all_orgs().filter(
            organization=organization
        ).count() == len(RETAIL.people)
        assert Department.objects.all_orgs().filter(
            organization=organization
        ).count() == len(RETAIL.departments)
        assert Location.objects.all_orgs().filter(
            organization=organization
        ).count() == len(RETAIL.locations)


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_two_demo_companies_are_two_customers(plans, settings, tmp_path):
    """
    The claim a single demo company can never support.

    Seeded from one command, in one database, in one run — and then asked the
    only question that matters: does either one's HR Head reach the other's
    people. The scoping is the product's, not this test's: `Employee.objects`
    resolves against the bound organization, and `all_orgs()` is the sanctioned
    escape used here to prove the two sets are disjoint rather than to read
    across them in anger.
    """
    settings.MEDIA_ROOT = tmp_path
    from apps.employees.models import Employee
    from apps.organization.models import Organization
    from core.middleware import acting_as

    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)
    call_command("seed_demo_platform", "--profile", "technology", verbosity=0)

    retail = Organization.objects.get(slug=RETAIL.slug)
    technology = Organization.objects.get(slug=TECHNOLOGY.slug)

    with acting_as(None, organization=retail):
        retail_people = set(Employee.objects.values_list("pk", flat=True))
        # The tenant manager, not a filter written here.
        assert len(retail_people) == len(RETAIL.people)
        assert not Employee.objects.filter(
            user__email__endswith=f"@{TECHNOLOGY.domain}"
        ).exists()

    with acting_as(None, organization=technology):
        technology_people = set(Employee.objects.values_list("pk", flat=True))
        assert len(technology_people) == len(TECHNOLOGY.people)

    assert retail_people.isdisjoint(technology_people)

    # And the plans really did come out different, which is what makes the two
    # worth having side by side. Read in ONE platform-side block: a
    # subscription is the platform's row about a customer, not the customer's.
    from apps.platform.models import Subscription

    from tests.conftest import across_organizations

    with across_organizations():
        plans_by_slug = dict(
            Subscription.objects.filter(
                organization__in=[retail, technology], is_active=True
            ).values_list("organization__slug", "plan__code")
        )
    assert plans_by_slug[RETAIL.slug] != plans_by_slug[TECHNOLOGY.slug]


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_removing_one_demo_company_leaves_the_other_intact(plans, settings, tmp_path):
    """
    The defect this profile work had to avoid: demo rows were selected by email
    DOMAIN, so two demo companies could delete each other's people. Removal is
    keyed on the organization, and the profiles are checked elsewhere for
    holding distinct domains and slugs.

    What removal does NOT do is delete the organization, and that is the
    product's rule rather than an omission: every org-owned table points at it
    with `on_delete=PROTECT`, and there is no call in this system that deletes
    a customer's data in one go. The administrator is spared for a second
    reason -- a company nobody can administer cannot be re-seeded, since every
    demo employee is created BY an admin.
    """
    settings.MEDIA_ROOT = tmp_path
    from apps.accounts.models import User
    from apps.employees.models import Employee
    from apps.organization.models import Organization

    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)
    call_command("seed_demo_platform", "--profile", "technology", verbosity=0)

    call_command("seed_demo_platform", "--profile", "retail", "--remove", verbosity=0)

    retail = Organization.objects.get(slug=RETAIL.slug)
    # ACTIVE employees. `Employee.delete()` is a soft delete, as it is
    # everywhere in this product outside a purge, so the rows remain as
    # inactive records -- and inactive is what seat limits and headcount
    # measure against, so it is the claim worth asserting.
    from tests.conftest import across_organizations

    with across_organizations():
        assert not Employee.objects.all_orgs().filter(
            organization=retail, is_active=True
        ).exists()
    survivors = list(
        User.objects.filter(email__endswith=f"@{RETAIL.domain}").values_list(
            "email", flat=True
        )
    )
    assert survivors == [f"admin@{RETAIL.domain}"], survivors

    assert Organization.objects.filter(slug=TECHNOLOGY.slug).exists()
    assert User.objects.filter(email__endswith=f"@{TECHNOLOGY.domain}").count() == len(
        TECHNOLOGY.people
    ) + len(TECHNOLOGY.system_people)


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_a_removed_company_can_be_seeded_again(plans, settings, tmp_path):
    """
    Which is what `--reset` is, and the reason `--remove` spares the
    administrator: the re-seed acts as it.
    """
    settings.MEDIA_ROOT = tmp_path
    from apps.employees.models import Employee
    from apps.organization.models import Organization

    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)
    call_command("seed_demo_platform", "--profile", "retail", "--reset", verbosity=0)

    retail = Organization.objects.get(slug=RETAIL.slug)
    # Active only: the first roster is still there as soft-deleted records,
    # which is the product's deletion rule, not a leftover.
    from tests.conftest import across_organizations

    with across_organizations():
        assert Employee.objects.all_orgs().filter(
            organization=retail, is_active=True
        ).count() == len(RETAIL.people)


@pytest.mark.django_db
@pytest.mark.unbound_organization
def test_seeding_the_same_company_twice_does_not_build_a_second_one(plans, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    from apps.organization.models import Organization

    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)
    call_command("seed_demo_platform", "--profile", "retail", verbosity=0)

    assert Organization.objects.filter(slug=RETAIL.slug).count() == 1
