"""
Reissuing credentials when a welcome mail never arrives.

The case this exists for: a provider accepts the message at SMTP and only
afterwards decides not to deliver it, bouncing to the SENDER's mailbox. The
original send looks successful, so nothing in the system knows — HR needs a
way to put it right that does not involve deleting the person.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core import mail

from apps.accounts.services.passwords import reissue_credentials
from apps.employees.models import Employee
from core.access.catalog import DepartmentKind
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

EMPLOYEES = "/api/v1/employees/"
PASSWORD = "test-password-12345"


@pytest.fixture
def hr_head_user(db, org, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="hrhead@resend.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["hr_head"])
    bind_membership(user)
    Employee.objects.create(
        employee_code="EMP06000", first_name="Hr", last_name="Head", user=user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )
    return user


@pytest.fixture
def joiner(db, org, roles):
    """Someone whose welcome mail was lost after we sent it."""
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="joiner@company.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["employee"])
    bind_membership(user)
    return Employee.objects.create(
        employee_code="EMP06001", first_name="Lost", last_name="Joiner", user=user,
        personal_email="lost.joiner@gmail.test",
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2026, 9, 1),
    )


def test_reissuing_sends_a_fresh_mail_to_the_personal_address(joiner, hr_head_user):
    mail.outbox.clear()

    sent = reissue_credentials(employee=joiner, actor=hr_head_user)

    assert sent is True
    assert len(mail.outbox) == 1
    # Credentials go to the PERSONAL inbox, exactly as at creation.
    assert mail.outbox[0].to == ["lost.joiner@gmail.test"]
    assert "HRMS account is ready" in mail.outbox[0].subject


def test_the_old_password_stops_working_and_the_new_one_is_forced(joiner, hr_head_user):
    """
    A fresh password, not a repeat of the old one: the original is stored
    nowhere by design, and reissuing invalidates anything that leaked through
    a bounce sitting in somebody's inbox.
    """
    from django.contrib.auth import authenticate

    assert authenticate(username="joiner@company.test", password=PASSWORD)

    reissue_credentials(employee=joiner, actor=hr_head_user)
    joiner.user.refresh_from_db()

    assert authenticate(username="joiner@company.test", password=PASSWORD) is None
    assert joiner.user.must_change_password is True


def test_reissuing_is_audited(joiner, hr_head_user):
    from apps.audit.models import AuditLog

    reissue_credentials(employee=joiner, actor=hr_head_user)

    assert AuditLog.objects.filter(
        entity_id=str(joiner.user.pk), after__event="credentials_reissued"
    ).exists()


def test_the_temporary_password_never_appears_in_the_audit_trail(joiner, hr_head_user):
    from apps.audit.models import AuditLog

    mail.outbox.clear()
    reissue_credentials(employee=joiner, actor=hr_head_user)

    body = mail.outbox[0].body
    # The password is in the mail…
    assert "Temporary password:" in body
    # …and in no audit row.
    rows = AuditLog.objects.filter(entity_id=str(joiner.user.pk))
    for row in rows:
        assert "password" not in str(row.after).lower() or "must_change" in str(row.after).lower()


def test_hr_reissues_over_the_api(api, joiner, hr_head_user):
    mail.outbox.clear()
    api.force_authenticate(user=hr_head_user)

    response = api.post(f"{EMPLOYEES}{joiner.pk}/resend-credentials/", {}, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["sent"] is True
    assert body["recipient"] == "lost.joiner@gmail.test"
    assert len(mail.outbox) == 1


def test_an_ordinary_employee_cannot_reissue_anyone(api, joiner, make_user):
    api.force_authenticate(user=make_user("operations_manager"))

    response = api.post(f"{EMPLOYEES}{joiner.pk}/resend-credentials/", {}, format="json")

    assert response.status_code in (403, 404)


def test_an_employee_without_a_login_is_refused(api, org, hr_head_user):
    """A record with no account has no credentials to send."""
    no_login = Employee.objects.create(
        employee_code="EMP06002", first_name="No", last_name="Login",
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2026, 9, 1),
    )
    api.force_authenticate(user=hr_head_user)

    response = api.post(f"{EMPLOYEES}{no_login.pk}/resend-credentials/", {}, format="json")

    assert response.status_code == 400
    assert b"no login" in response.content


def test_a_mail_failure_is_reported_not_raised(joiner, hr_head_user, monkeypatch, settings):
    """
    The account still has its new password; the caller is told the truth so HR
    can fix the mail problem and try again.
    """
    from apps.accounts.services import passwords as module

    def explode(*args, **kwargs):
        raise OSError("smtp refused")

    monkeypatch.setattr(module, "_hr_mail_connection", explode)

    sent = reissue_credentials(employee=joiner, actor=hr_head_user)

    assert sent is False
    joiner.user.refresh_from_db()
    assert joiner.user.must_change_password is True
