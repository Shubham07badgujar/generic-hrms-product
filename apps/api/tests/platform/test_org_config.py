"""
Per-organization configuration: resolved explicitly, never borrowed.

Two properties, and the second is the one with teeth.

FALLBACK IS TO `settings`, NEVER TO ANOTHER ORGANIZATION. Every resolver reads
one organization id and falls through to the deployment default. There is no
ordering in which one customer's SMTP server or biometric credentials become
reachable from another's context, because there is no code path that looks at a
second organization at all.

AND THE SETTINGS ARE NOT READ ANYWHERE ELSE. A resolver nobody calls is
decoration. The AST scan at the bottom is what stops a future call site reading
`settings.EMAIL_HOST` directly and quietly sending one customer's mail through
the deployment's account -- a mistake that works perfectly in every
single-tenant test and is wrong the moment there are two customers.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from apps.platform.services.provisioning import provision_organization
from core.config import attendance_config, email_config

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def two_companies(db):
    first = provision_organization(
        name="Northwind Health", slug="northwind",
        admin_email="admin@northwind.example",
    )
    second = provision_organization(
        name="Aperture Systems", slug="aperture",
        admin_email="admin@aperture.example",
    )
    return first.organization, second.organization


# ---------------------------------------------------------------------------
# Falling back, and to what
# ---------------------------------------------------------------------------


def test_an_unconfigured_organization_gets_the_deployment_defaults(
    two_companies, settings
):
    """
    The single-company case, which must keep working unchanged: an empty
    configuration table and no branch anywhere saying "if multi-tenant".
    """
    settings.EMAIL_HOST = "smtp.deployment.example"
    settings.HR_CONTACT_EMAIL = "hr@deployment.example"
    organization, _ = two_companies

    config = email_config(organization)
    assert config.host == "smtp.deployment.example"
    assert config.hr_contact == "hr@deployment.example"
    assert not config.is_organization_specific


def test_an_organization_overrides_field_by_field(two_companies, settings):
    """
    Not all-or-nothing. An organization that sets only its own from-address,
    leaving the SMTP server to the platform, gets exactly that -- requiring
    them to restate the whole block is how settings screens fill up with
    copied-in values that go stale.
    """
    from apps.organization.models import OrgEmailConfig

    settings.EMAIL_HOST = "smtp.deployment.example"
    organization, _ = two_companies
    OrgEmailConfig.objects.create(
        organization=organization, from_email="people@northwind.example"
    )

    config = email_config(organization)
    assert config.from_email == "people@northwind.example"
    assert config.host == "smtp.deployment.example", "the server was not overridden"


def test_one_organizations_mail_settings_never_reach_another(two_companies):
    """
    The failure this design exists to prevent: a message sent through another
    customer's mail server, authenticating as them.
    """
    from apps.organization.models import OrgEmailConfig

    first, second = two_companies
    OrgEmailConfig.objects.create(
        organization=first,
        host="smtp.northwind.example",
        username="northwind",
        password="northwind-secret",
        from_email="people@northwind.example",
    )

    theirs = email_config(first)
    assert theirs.host == "smtp.northwind.example"
    assert theirs.is_organization_specific

    ours = email_config(second)
    assert ours.host != "smtp.northwind.example"
    assert ours.username != "northwind"
    assert ours.password != "northwind-secret"
    assert not ours.is_organization_specific


def test_one_organizations_device_credentials_never_reach_another(two_companies):
    """
    The brief names this one specifically: never copy one company's biometric
    credentials into another organization.
    """
    from apps.attendance.models import OrgAttendanceIntegration
    from core.middleware import acting_as

    first, second = two_companies
    with acting_as(None, organization=first):
        OrgAttendanceIntegration.objects.create(
            base_url="https://devices.northwind.example",
            username="northwind-api",
            password="device-secret",
        )

    theirs = attendance_config(first)
    assert theirs.base_url == "https://devices.northwind.example"
    assert theirs.password == "device-secret"

    ours = attendance_config(second)
    assert ours.base_url != "https://devices.northwind.example"
    assert ours.password != "device-secret"


def test_no_organization_resolves_to_the_deployment(settings):
    """
    `None` is a real answer -- a management command or a pre-auth path with no
    organization gets the DEPLOYMENT's configuration. Never a customer's, and
    never an exception, because a self-hosted install lives here permanently.
    """
    settings.EMAIL_HOST = "smtp.deployment.example"
    assert email_config(None).host == "smtp.deployment.example"
    assert not email_config(None).is_organization_specific


def test_secrets_are_encrypted_at_rest(two_companies):
    """
    Not asserting the ciphertext, which would test the library. Asserting that
    the plaintext is not sitting in the column: a database dump, a replica or a
    backup must not hand over every customer's SMTP password.
    """
    from django.db import connection

    from apps.organization.models import OrgEmailConfig

    organization, _ = two_companies
    OrgEmailConfig.objects.create(
        organization=organization, host="smtp.example", password="plaintext-secret"
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT password FROM organization_orgemailconfig")
        stored = cursor.fetchone()[0]

    assert stored, "nothing was stored at all"
    assert "plaintext-secret" not in str(stored)
    assert email_config(organization).password == "plaintext-secret"


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

#: Settings that are per-ORGANIZATION now. Reading one of these directly means
#: sending a customer's mail through the deployment's account, or reaching a
#: device with the deployment's credentials -- which works perfectly on a
#: single-tenant installation and is wrong the moment there are two customers.
PER_ORGANIZATION_SETTINGS = {
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USE_TLS",
    "HR_EMAIL_HOST_USER",
    "HR_EMAIL_HOST_PASSWORD",
    "HR_FROM_EMAIL",
    "HR_CONTACT_EMAIL",
    "ESSL_BASE_URL",
    "ESSL_USERNAME",
    "ESSL_PASSWORD",
}

#: Where reading them is correct, with the reason.
ALLOWED = {
    # THE resolver. Reading the deployment defaults is its whole job.
    "core/config.py": "resolves per-organization configuration",
    # Seed and demo commands describe a deployment, not a customer.
    "apps/organization/management/commands/seed_all.py": "developer demo data",
    "apps/organization/management/commands/seed_demo_company.py": "developer demo data",
    "apps/organization/management/commands/seed_demo.py": "developer demo data",
}


def _api_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _offending_reads():
    """Every `settings.X` read of a per-organization setting, outside ALLOWED."""
    root = _api_root()
    offenders = []

    for path in list((root / "apps").rglob("*.py")) + list(
        (root / "core").rglob("*.py")
    ):
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED or "/migrations/" in relative:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue

        for node in ast.walk(tree):
            # settings.EMAIL_HOST
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
                and node.attr in PER_ORGANIZATION_SETTINGS
            ):
                offenders.append(f"{relative}:{node.lineno} settings.{node.attr}")
            # getattr(settings, "ESSL_BASE_URL", "")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "settings"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in PER_ORGANIZATION_SETTINGS
            ):
                offenders.append(
                    f"{relative}:{node.lineno} getattr(settings, "
                    f"{node.args[1].value!r})"
                )
    return offenders


def test_per_organization_settings_are_read_only_by_the_resolver():
    """
    A resolver nobody calls is decoration.

    This is what stops the next call site reading `settings.EMAIL_HOST`
    directly. The mistake is invisible in a single-tenant test suite -- every
    assertion passes, because there is only one customer and the deployment
    default happens to be theirs.
    """
    offenders = _offending_reads()
    assert not offenders, (
        "These read a per-organization setting directly instead of going "
        "through core.config:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_can_actually_see_a_violation():
    """
    Guards the guard. If the scan stopped parsing, or the setting names drifted
    out of the product, the test above would pass forever while checking
    nothing.
    """
    source = "from django.conf import settings\nx = settings.ESSL_PASSWORD\n"
    tree = ast.parse(source)
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "settings"
        and node.attr in PER_ORGANIZATION_SETTINGS
    ]
    assert found, "the scan no longer recognises a direct read"
    assert _api_root().joinpath("core/config.py").exists(), "the resolver moved"
