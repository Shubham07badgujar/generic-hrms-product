"""
The Platform / Organization boundary, asserted from both sides.

The brief's governing sentence is that the system has two separated security
domains, and that **Platform Admin does not receive implicit access to customer
HR data**. Everything here is that sentence, made falsifiable.

Both directions are tested, because only testing one is how a boundary rots:

  * an organization principal -- including the Admin, who holds `Scope.ALL`
    almost everywhere -- reaches nothing under `/api/v1/platform/`;
  * the platform operator reaches nothing that belongs to a customer, and is
    not merely *unlikely* to: they hold no role anywhere, so there is no
    matrix edit an organization Admin could make that would change it.

The build-time half matters as much as the runtime half. A future view can be
added to the platform tree without the declaration that protects it, and
`access.E011` is what turns that into a failed build rather than an open route.
Those checks are exercised here against deliberately wrong views, so that a
check reduced to a no-op fails this file instead of passing everything forever.
"""

from __future__ import annotations

import pytest
from rest_framework.views import APIView

from core.access.checks import check_one_view
from core.access.drf import PlatformAPIView

#: `unbound_organization` for the same reason the tenancy suite uses it: a
#: test about a boundary must not inherit its tenant from a fixture.
pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


# ---------------------------------------------------------------------------
# Build time
# ---------------------------------------------------------------------------


def _ids(errors):
    return {e.id for e in errors}


def test_a_platform_url_without_the_declaration_fails_the_build():
    """
    The failure this check exists for: somebody adds a view to the platform
    tree and forgets what makes it a platform view.

    It would not be *open* -- `RBACPermission` would look for an
    `access_resource` it does not have and deny everyone -- but it would be a
    route in the platform console that nobody can use, discovered by a support
    ticket rather than by the build.
    """

    class ForgotTheDeclaration(APIView):
        pass

    errors = check_one_view(ForgotTheDeclaration, "api/v1/platform/plans/")
    assert "access.E011" in _ids(errors), errors


def test_the_declaration_outside_the_platform_tree_fails_the_build():
    """
    The opposite mistake, and the more dangerous one.

    A `platform_only` view mounted on an organization URL is reachable only by
    the operator, which is *safe*, but it puts platform authority somewhere no
    reader of the route table would look for it. The two trees stay disjoint
    so that "is this the platform?" is answerable from a URL alone -- in a log
    line, an access log, a proxy rule.
    """

    class PlatformViewInTheWrongPlace(PlatformAPIView):
        pass

    errors = check_one_view(PlatformViewInTheWrongPlace, "api/v1/employees/")
    assert "access.E012" in _ids(errors), errors


def test_a_platform_view_that_is_also_exempt_fails_the_build():
    """
    `access_exempt` means "no RBAC at all".

    On a platform view it would be an open route wearing a locked-looking
    name, and invisible to every other check in this file, because they all
    return early for exempt views.
    """

    class BothFlags(PlatformAPIView):
        access_exempt = True

    errors = check_one_view(BothFlags, "api/v1/platform/plans/")
    assert "access.E013" in _ids(errors), errors


def test_a_correct_platform_view_needs_no_resource():
    """
    The allowance that makes the design possible.

    A platform view deliberately declares no `access_resource`, because
    PLAN and ORGANIZATION must never become rows in the runtime-editable
    permission matrix -- an organization Admin editing their own roles could
    otherwise grant themselves platform authority. So `access.E001` must NOT
    fire here, and this asserts the allowance is real rather than assumed.
    """

    class ProperlyDeclared(PlatformAPIView):
        pass

    assert check_one_view(ProperlyDeclared, "api/v1/platform/organizations/") == []


def test_every_real_platform_route_declares_itself():
    """
    Not a fabricated view: the routes actually mounted right now.

    `check_one_view` is called per class by the resolver walk, which dedupes by
    class name -- so this walks the platform tree directly and judges what is
    really there.
    """
    from django.urls import URLPattern, URLResolver, get_resolver

    from core.access.permissions import PLATFORM_PATH_PREFIX

    offenders = []

    def walk(resolver, prefix=""):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry, prefix + str(entry.pattern))
                continue
            if not isinstance(entry, URLPattern):
                continue
            path = "/" + prefix + str(entry.pattern)
            if not path.startswith(PLATFORM_PATH_PREFIX):
                continue
            callback = getattr(entry, "callback", None)
            view = getattr(callback, "cls", None) or getattr(
                callback, "view_class", None
            )
            if view is None or view.__name__ == "APIRootView":
                continue
            if not getattr(view, "platform_only", False):
                offenders.append(f"{path} -> {view.__name__}")

    walk(get_resolver())
    assert not offenders, (
        f"Mounted under {PLATFORM_PATH_PREFIX} without `platform_only`: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    """A platform admin, created the only way the flag can be set."""
    from django.core.management import call_command

    from apps.accounts.models import User

    call_command(
        "bootstrap_platform_admin", "--email", "ops@platform.example",
        "--first-name", "Ops",
    )
    user = User.objects.get(email="ops@platform.example")
    user.set_password("operator-password-12345")
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


def _client_for(email, password, path="/api/v1/auth/login/"):
    from rest_framework.test import APIClient

    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        path, {"email": email, "password": password}, format="json"
    )
    if response.status_code != 200:
        return None, response
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client, response


def test_the_operator_signs_in_at_the_platform_door_and_nowhere_else(operator):
    """
    One door per domain, tested as an equality rather than a one-way gate.

    Letting the operator in at the organization door would not GRANT them
    anything -- authority is re-derived from the flag on every request -- but
    it would hand them a session that 403s on every screen, which is a
    confusing dead end rather than a refusal.
    """
    platform_client, _ = _client_for(
        operator.email, "operator-password-12345", "/api/v1/auth/login/platform/"
    )
    assert platform_client is not None, "the operator cannot reach their own door"

    for door in ("/api/v1/auth/login/", "/api/v1/auth/login/admin/"):
        client, response = _client_for(operator.email, "operator-password-12345", door)
        assert client is None, f"the operator was admitted at {door}"
        assert response.status_code == 400, response.status_code


def test_an_organization_admin_is_refused_at_the_platform_door(org_a, api_for):
    """And gets the same generic error as a wrong password, so the entrance
    never confirms which addresses belong to the operator."""
    client, response = _client_for(
        org_a.admin.email, "test-password-12345", "/api/v1/auth/login/platform/"
    )
    assert client is None
    assert response.status_code == 400, response.content[:200]


def test_an_organization_admin_reaches_nothing_on_the_platform(org_a, api_for):
    """
    The Admin deliberately: they hold `Scope.ALL` on almost every resource in
    their own organization, which is the most authority any customer principal
    can have.
    """
    client = api_for(org_a.admin)
    for path in ("/api/v1/platform/organizations/", "/api/v1/platform/summary/"):
        response = client.get(path)
        assert response.status_code == 403, (path, response.status_code)


def test_the_operator_sees_the_organizations_they_administer(operator, org_a, org_b):
    """
    The one queryset in the product that is SUPPOSED to span tenants, because
    organizations are what a platform operator administers.
    """
    client, _ = _client_for(
        operator.email, "operator-password-12345", "/api/v1/auth/login/platform/"
    )
    response = client.get("/api/v1/platform/organizations/")
    assert response.status_code == 200, response.content[:200]

    body = response.json()
    rows = body.get("data", body) if isinstance(body, dict) else body
    slugs = {row["slug"] for row in rows}
    assert {org_a.slug, org_b.slug} <= slugs, slugs


def test_the_operator_reaches_no_customer_hr_data(operator, org_a, org_b):
    """
    The brief's rule, and the reason the platform principal is a flag rather
    than a role: they hold no grant in any organization, so there is no matrix
    edit that could turn this test green the wrong way.
    """
    client, _ = _client_for(
        operator.email, "operator-password-12345", "/api/v1/auth/login/platform/"
    )

    listings = [
        "/api/v1/employees/",
        "/api/v1/leave-requests/",
        "/api/v1/attendance-records/",
        "/api/v1/payroll/runs/",
        "/api/v1/candidates/",
        "/api/v1/employee-documents/",
        "/api/v1/audit/",
        "/api/v1/departments/",
    ]
    served = [p for p in listings if client.get(p).status_code == 200]
    assert not served, f"the platform operator was served customer data at {served}"

    # And not by naming a row directly either.
    for label in ("employee", "leave_request", "payroll_run", "candidate"):
        row = org_a.rows[label]
        path = {
            "employee": f"/api/v1/employees/{row.pk}/",
            "leave_request": f"/api/v1/leave-requests/{row.pk}/",
            "payroll_run": f"/api/v1/payroll/runs/{row.pk}/",
            "candidate": f"/api/v1/candidates/{row.pk}/",
        }[label]
        assert client.get(path).status_code in (403, 404), label


def test_promoting_a_customers_user_is_refused(org_a):
    """
    One person holding both a membership and platform authority is the single
    principal the whole domain split exists to prevent -- and it would not even
    work: `resolve_context` short-circuits to the platform context, so their
    own organization would silently go dark.
    """
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="belongs to an organization"):
        call_command("bootstrap_platform_admin", "--email", org_a.admin.email)

    org_a.admin.refresh_from_db()
    assert not org_a.admin.is_platform_admin


def test_the_flag_cannot_travel_through_a_serializer():
    """
    Platform authority is not grantable over the network, by anyone.

    There is no user-administration API today, so asserting that a request
    cannot set the flag would be asserting that a route does not exist -- true,
    and worthless the day somebody adds one. The durable guarantee is upstream:
    the field is `editable=False`, which is what makes DRF's `ModelSerializer`
    refuse to generate it and Django's `ModelForm` refuse to include it.

    So this builds the most permissive serializer the framework offers, over
    the model itself, and asserts the field cannot be WRITTEN through it. Note
    the correction that finding this cost: DRF does not drop a non-editable
    field, it emits it as `read_only`. Absence was the wrong assertion --
    unwritability is the right one, and it is the one that actually protects
    anything.

    A future user API built the ordinary way inherits that refusal. One that
    declares the field explicitly has to write the field name down, which is
    greppable and reviewable in a way `fields = "__all__"` is not.
    """
    from rest_framework import serializers

    from apps.accounts.models import User

    class EverythingSerializer(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = "__all__"

    field = EverythingSerializer().get_fields()["is_platform_admin"]
    assert field.read_only, (
        "`fields = \"__all__\"` produced a WRITABLE is_platform_admin. Platform "
        "authority would then be grantable by any request reaching such a "
        "serializer."
    )
    assert User._meta.get_field("is_platform_admin").editable is False
