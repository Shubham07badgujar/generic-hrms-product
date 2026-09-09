"""Shared pytest fixtures."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True, scope="session")
def _fast_password_hashing():
    """
    Use a cheap hasher for the whole test session.

    Argon2 is deliberately expensive — correct for production, ruinous for a
    suite that creates and authenticates users hundreds of times. Django still
    exercises the same code path; only the work factor changes.

    Session-scoped and applied via django.conf so it covers module-level user
    creation too, not just tests that request the `settings` fixture.
    """
    from django.conf import settings as django_settings

    original = django_settings.PASSWORD_HASHERS
    django_settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher"
    ]
    yield
    django_settings.PASSWORD_HASHERS = original


@pytest.fixture(autouse=True)
def _statutory_strict(settings):
    """Tests default to production posture: unverified rule sets are refused."""
    settings.STATUTORY_ALLOW_UNVERIFIED = False


@pytest.fixture(autouse=True)
def _no_throttling(settings):
    """
    Throttles off by default.

    Rate limits are shared per-scope across a test session, so a suite that
    logs in repeatedly would start 429-ing partway through and fail for reasons
    unrelated to what it tests. Throttling is exercised explicitly where it is
    the subject.

    Swapping the rates is not sufficient on its own. DRF binds
    `SimpleRateThrottle.THROTTLE_RATES` as a CLASS attribute when
    `rest_framework.throttling` is first imported, so a test that reloads a
    urls module rebuilds its view classes against whatever rates were live at
    that moment — a rate can come back from the dead mid-session. And the
    counters themselves live in the cache, which the database rollback that
    isolates everything else does not touch.

    Clearing the cache around every test closes both gaps: whatever the rate
    turns out to be, no test inherits another test's tally.
    """
    from django.core.cache import cache

    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            k: None for k in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        },
    }

    cache.clear()
    yield
    cache.clear()


#: Fixed so the session organization is stable, greppable, and obviously a
#: fixture rather than something a test happened to create.
SESSION_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


@pytest.fixture(autouse=True)
def _bind_session_organization(request):
    """
    Bind the session organization for the duration of every test.

    Tenant-owned models take their organization from the acting context when
    they are created, and in production that context is bound by the access
    layer on every authenticated request. Tests build rows directly, outside
    any request, so without this they hit `OrgContextMissing` -- correctly, and
    uselessly, in hundreds of places that are not about tenancy at all.

    Uses the fixed id rather than querying, so it costs nothing and works in
    tests that never touch the database.

    The cross-tenant suites deliberately do NOT rely on this: they bind their
    own organizations explicitly with `acting_as`, because a test about
    isolation must not inherit its tenant from a fixture.
    """
    from core.middleware import _current_org

    if request.node.get_closest_marker("unbound_organization"):
        # Tests ABOUT the binding must not be handed one. Marked rather than
        # opted into, so the default stays "bound" for the hundreds of tests
        # that are not about tenancy.
        yield
        return

    token = _current_org.set(SESSION_ORG_ID)
    try:
        yield
    finally:
        _current_org.reset(token)


@pytest.fixture(scope="session")
def _platform_seed(django_db_setup, django_db_blocker):
    """
    One organization, committed once for the whole session.

    Every principal now resolves its tenant through an
    `OrganizationMembership`, so essentially every test needs an organization
    to exist. Creating one per test would be fine on its own, but the role
    catalogue below is session-scoped for speed and roles will shortly hang off
    an organization too -- so the organization has to outlive a single test's
    transaction for the same reason the roles do.
    """
    from apps.organization.models import Organization, OrgStatus

    with django_db_blocker.unblock():
        Organization.objects.get_or_create(
            id=SESSION_ORG_ID,
            defaults={
                "name": "Test Clinic",
                "slug": "test-clinic",
                "status": OrgStatus.ACTIVE,
            },
        )


@pytest.fixture
def organization(db, _platform_seed):
    """The organization every fixture-built principal belongs to."""
    from apps.organization.models import Organization

    return Organization.objects.get(pk=SESSION_ORG_ID)


@pytest.fixture(scope="session")
def _seeded_roles(django_db_setup, django_db_blocker, _platform_seed):
    """
    Seed the role catalogue ONCE for the whole session.

    Seeding writes 18 roles and ~834 permission rows. Doing that per test made
    the suite an order of magnitude slower than the behaviour it was testing.
    Committed outside the per-test transaction, so every test still sees it
    while its own writes roll back as usual.
    """
    from apps.accounts.services.roles import seed_roles
    from apps.organization.models import Organization
    from core.middleware import acting_as

    with django_db_blocker.unblock(), acting_as(None, organization=SESSION_ORG_ID):
        seed_roles(organization=Organization.objects.get(pk=SESSION_ORG_ID))


@pytest.fixture
def roles(db, _seeded_roles, _platform_seed):
    """The 18 canonical roles, keyed by code."""
    from apps.accounts.models import Role

    return {r.code: r for r in Role.objects.all()}


def session_organization():
    """The session organization, fetched fresh so it is never a stale instance."""
    from apps.organization.models import Organization

    return Organization.objects.get(pk=SESSION_ORG_ID)


def bind_membership(user, organization=None):
    """
    Give a hand-built principal its tenant identity.

    Most suites build their people directly rather than through `make_user`,
    because they need an Employee attached or are deliberately exercising the
    chicken-and-egg cases the provisioning services exist to solve. Those users
    still need a membership: it is the ONLY thing `resolve_context()` reads to
    decide which organization a principal acts in, so without one they resolve
    to DENY_ALL and every request they make returns 403.

    Idempotent, so a fixture that layers a second role onto an existing user
    can call it without caring whether it already ran.
    """
    from apps.organization.models import OrganizationMembership

    membership, _ = OrganizationMembership.objects.get_or_create(
        organization=organization or session_organization(), user=user
    )
    return membership


@pytest.fixture
def make_user(db, roles, _platform_seed):
    """
    Build a user holding a given role.

    `link_employee` attaches a minimal Employee so department- and team-scoped
    roles resolve to something. Roles flagged `requires_employee=False`
    (admin, ceo) work without one — which is exactly the distinction the
    access engine encodes, so tests should be able to exercise both.
    """
    from apps.accounts.models import User, UserRole
    from apps.organization.models import OrganizationMembership

    created: list = []

    def _make(role_code: str, *, email: str | None = None, organization=None, **extra):
        email = email or f"{role_code}@example.test"
        user = User.objects.create_user(
            email=email, password="test-password-12345", **extra
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        # Without a membership the access engine resolves DENY_ALL, by design:
        # tenant identity comes from this row and nowhere else. `organization`
        # is a keyword with a default so every existing caller is unchanged and
        # the cross-tenant tests can still ask for a second one explicitly.
        OrganizationMembership.objects.create(
            organization=organization or session_organization(), user=user
        )
        created.append(user)
        return user

    return _make


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def org(db, _platform_seed):
    """
    The four functional departments, plus levels, a location and a designation.

    Shared by the employee and recruitment suites, both of which need a real
    organisational structure for department-scoped rules to mean anything.
    """
    from apps.organization.models import (
        Department,
        Designation,
        EmployeeLevel,
        Location,
        OrgSettings,
    )
    from core.access.catalog import DepartmentKind, Layer

    # Start the code allocator above the codes fixtures assign by hand, so a
    # fixture-created manager never collides with an auto-allocated new hire.
    settings_row = OrgSettings.for_org(session_organization())
    settings_row.employee_code_prefix = "EMP"
    settings_row.employee_code_next = 5000
    settings_row.save(update_fields=["employee_code_prefix", "employee_code_next"])

    departments = {
        kind: Department.objects.create(
            name=f"{DepartmentKind(kind).label} Department",
            code=kind.upper()[:8],
            kind=kind,
        )
        for kind in (
            DepartmentKind.MEDICAL,
            DepartmentKind.OPERATIONS,
            DepartmentKind.HR,
            DepartmentKind.FINANCE,
        )
    }

    levels = {
        layer: EmployeeLevel.objects.create(
            name=Layer(layer).label, code=f"L{layer}", layer=layer
        )
        for layer in (
            Layer.DEPARTMENT_HEAD,
            Layer.MANAGER,
            Layer.EXECUTIVE,
            Layer.STAFF,
        )
    }

    location = Location.objects.create(
        name="Head Office", code="HO", city="Mumbai", state="MH", is_head_office=True
    )
    designation = Designation.objects.create(
        title="Physiotherapist", department=departments[DepartmentKind.MEDICAL]
    )
    # A title belonging to no department in particular. Creation now REQUIRES a
    # designation, so every test that builds an employee needs one — including
    # the many that are about something else entirely and place people in HR or
    # Finance. Reaching for the medical title above would make those tests fail
    # on the designation/department rule for a reason they never meant to
    # exercise. That rule has its own test; this keeps it out of everyone
    # else's way.
    any_designation = Designation.objects.create(title="Staff Member", department=None)

    return {
        "organization": session_organization(),
        "departments": departments,
        "levels": levels,
        "location": location,
        "designation": designation,
        "any_designation": any_designation,
    }


@pytest.fixture
def first_login_done():
    """
    Mark a freshly created login as past its forced password change.

    `create_employee` now flags every new account `must_change_password`, and
    the API refuses everything but the change screen until it is cleared —
    which is the feature, not a fixture problem. Tests that sign in AS a new
    hire to exercise self-service scope represent a person who has already
    chosen their password, so they say so explicitly here rather than
    inheriting a state that only exists during onboarding.
    """

    def _done(user):
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        return user

    return _done
