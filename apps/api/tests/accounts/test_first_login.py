"""
Account creation, the temporary password, and the forced first-login change.

The claims:

  1. Creating an employee creates a login with a GENERATED temporary password
     and mails it — after commit, never before.
  2. Until the person replaces it, that password unlocks exactly one thing:
     replacing itself. Every other authenticated request is refused with a
     specific code, at the API, regardless of role.
  3. Once replaced, the temporary password verifies against nothing, every
     prior session is dead, and the account is unrestricted — subject only to
     the ordinary matrix, which this feature does not touch.
  4. Every step is audited without ever writing a secret down.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils.html import escape

from apps.accounts.models import User
from apps.accounts.services import passwords
from apps.audit.models import AuditLog
from apps.employees.services.creation import create_employee
from core.access.catalog import DepartmentKind, Layer

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CHANGE = "/api/v1/auth/change-password/"


@pytest.fixture
def hr_head(make_user, org):
    from apps.employees.models import Employee

    user = make_user("hr_head")
    Employee.objects.create(
        employee_code="EMP07800",
        user=user,
        first_name="Hema",
        department=org["departments"][DepartmentKind.HR],
        date_of_joining="2020-01-01",
    )
    return user


@pytest.fixture
def designation(db):
    """Every employee created through the ordinary path needs a job title now."""
    from apps.organization.models import Designation

    return Designation.objects.create(title="Therapist")


@pytest.fixture
def director(make_user, org):
    """Somebody for the new hires to report to — the hierarchy demands one."""
    from apps.employees.models import Employee

    user = make_user("medical_director", email="director@example.test")
    return Employee.objects.create(
        employee_code="EMP07801",
        user=user,
        first_name="Meera",
        department=org["departments"][DepartmentKind.MEDICAL],
        date_of_joining="2019-01-01",
    )


@pytest.fixture
def created(hr_head, director, designation, org, django_capture_on_commit_callbacks):
    """A new employee, created the way the API does it, with commit hooks run."""
    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=hr_head,
            first_name="Nisha",
            last_name="Verma",
            email="nisha.verma@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )
    return result


# ------------------------------------------------------------- creation


def test_a_temporary_password_is_generated_when_none_is_given(created):
    assert created.temporary_password
    assert len(created.temporary_password) >= 12
    assert created.user.check_password(created.temporary_password)
    assert created.user.must_change_password is True


def test_generated_passwords_are_random(created, hr_head, org):
    """Two accounts, two secrets. Sanity, not statistics."""
    seen = {passwords.generate_temporary_password() for _ in range(20)}
    assert len(seen) == 20


def test_the_welcome_email_is_sent_after_commit(created):
    assert len(mail.outbox) == 1
    message = mail.outbox[0]

    assert message.to == ["nisha.verma@example.test"]
    assert "nisha.verma@example.test" in message.body  # the username
    assert created.temporary_password in message.body  # the one place it appears
    assert "/login" in message.body  # where to go


def test_the_subject_names_the_company_and_carries_a_reference(created):
    """
    "Account Details" is phishing's stock phrase; the employee code is a
    reference that marks the message as one-off transactional mail.
    """
    subject = mail.outbox[0].subject

    assert subject.startswith("Your Test Clinic HRMS account is ready")
    assert "Account Details" not in subject


def test_every_placeholder_resolves_to_real_data(created):
    """
    The template's variables, one by one. A template that ships with a
    placeholder still in it is the classic failure here, so the last
    assertion looks for the syntax itself.
    """
    message = mail.outbox[0]
    html = message.alternatives[0][0]

    for part in (message.body, html):
        assert "Nisha Verma" in part                    # employee_name
        assert "Test Clinic" in part                    # company_name
        assert "Therapist" in part                      # designation
        assert "https://" in part or "http://" in part  # login_url
        assert "/login" in part
        assert "nisha.verma@example.test" in part       # username
        assert "hr@" in part or "@" in part             # hr_contact_email
        # Nothing unresolved.
        assert "{{" not in part and "}}" not in part

    # temporary_password. The generated alphabet includes '&', which the HTML
    # part escapes to '&amp;' — correct, and what makes the password render as
    # itself in a mail client. Asserting the raw string against the HTML would
    # pass or fail on the luck of the draw, so each part is checked in its own
    # encoding. `test_an_ampersand_in_the_password_survives_the_html_part`
    # below removes the luck entirely.
    assert created.temporary_password in message.body
    assert escape(created.temporary_password) in html


def test_an_ampersand_in_the_password_survives_the_html_part(
    hr_head, director, designation, org, django_capture_on_commit_callbacks
):
    """
    '&' is in the generated password alphabet, and it is the one character in
    it that HTML escaping rewrites. Escaped is CORRECT — a client renders
    '&amp;' as '&' — but an unescaped one would silently corrupt the password
    for anyone reading the HTML part, and it would only bite on the fraction of
    accounts whose password happened to draw the character. Forced here so the
    guarantee does not depend on chance.
    """
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=hr_head,
            first_name="Amp",
            email="amp@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-03-01",
            temporary_password="Aa1&bb2&cc3&dd",
        )

    html = mail.outbox[0].alternatives[0][0]
    assert "Aa1&amp;bb2&amp;cc3&amp;dd" in html
    assert "Aa1&bb2&cc3&dd" in mail.outbox[0].body
    assert result.welcome_email_sent is True


def test_the_email_carries_both_a_text_and_an_html_part(created):
    message = mail.outbox[0]

    assert message.alternatives, "no HTML alternative attached"
    content, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert "<strong>" in content
    # The plain part must not be markup.
    assert "<div" not in message.body


def test_the_wording_matches_the_approved_template(created):
    body = mail.outbox[0].body

    assert body.startswith("Dear Nisha Verma,")
    assert "account on the Test Clinic HRMS has been created" in body
    assert "Position: Therapist" in body
    assert "HOW TO SIGN IN" in body
    assert "Temporary password:" in body
    # The trust signals that keep this out of a spam folder: an explicit
    # statement that we never ask for passwords, and a reason it was sent.
    assert "will never ask you for your password" in body
    assert "WHY YOU RECEIVED THIS" in body
    assert "nothing to unsubscribe from" in body
    assert "HR Team" in body


def test_the_designation_is_used_when_the_employee_has_one(
    hr_head, director, org, django_capture_on_commit_callbacks
):
    """The role is only the fallback; a real title wins."""
    from apps.organization.models import Designation

    title = Designation.objects.create(title="Senior Physiotherapist")
    with django_capture_on_commit_callbacks(execute=True):
        create_employee(
            actor=hr_head,
            first_name="Titled",
            last_name="Person",
            email="titled@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=title.pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    body = mail.outbox[-1].body
    assert "Position: Senior Physiotherapist" in body


def test_the_html_escapes_anything_a_name_might_contain(
    hr_head, director, designation, org, django_capture_on_commit_callbacks
):
    """A name is user input and lands in HTML; it must not become markup."""
    with django_capture_on_commit_callbacks(execute=True):
        create_employee(
            actor=hr_head,
            first_name="Ana<script>",
            last_name="O'Brien & Co",
            email="ana.obrien@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    html = mail.outbox[-1].alternatives[0][0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_a_rolled_back_creation_mails_nothing(
    hr_head, director, designation, org, django_capture_on_commit_callbacks
):
    """The hook is on_commit. If creation fails, no credentials go anywhere."""
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                create_employee(
                    actor=hr_head,
                    first_name="Ghost",
                    email="ghost@example.test",
                    role_code="therapist",
                    department_id=org["departments"][DepartmentKind.MEDICAL].pk,
                    level_id=org["levels"][Layer.STAFF].pk,
                    location_id=org["location"].pk,
                    designation_id=designation.pk,
                    reporting_manager_id=director.pk,
                    date_of_joining="2026-09-01",
                )
                raise RuntimeError("something after creation blew up")
        except RuntimeError:
            pass

    assert mail.outbox == []
    assert not User.objects.filter(email="ghost@example.test").exists()


def test_the_secret_is_never_written_to_the_audit_log(created):
    rows = AuditLog.objects.filter(entity_id=str(created.user.pk))
    assert rows.exists()
    blob = " ".join(str(row.after) + str(row.before) for row in rows)
    assert created.temporary_password not in blob


def test_creation_and_mail_are_audited(created):
    events = {row.after.get("event") for row in AuditLog.objects.filter(entity_id=str(created.user.pk))}
    assert "welcome_email_sent" in events


def test_the_audit_records_the_mailbox_the_mail_actually_went_to(
    hr_head, director, designation, org, django_capture_on_commit_callbacks
):
    """
    Credentials go to the PERSONAL address when one is on record, while the
    audit's `email` key names the login. Those differing is the normal case —
    and a mistyped personal address was undiagnosable from the trail alone,
    because the row never said where the mail went. Now it does.
    """
    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=hr_head,
            first_name="Personal",
            last_name="Mailbox",
            email="personal.login@example.test",
            personal_email="their.own@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    assert mail.outbox[-1].to == ["their.own@example.test"]

    row = next(
        r
        for r in AuditLog.objects.filter(entity_id=str(result.user.pk))
        if r.after.get("event") == "welcome_email_sent"
    )
    assert row.after["recipient"] == "their.own@example.test"
    assert row.after["email"] == "personal.login@example.test"


# ------------------------------------------------------ the gate, at the API


def test_the_temporary_password_logs_in_and_says_a_change_is_due(api, created):
    response = api.post(
        LOGIN, {"email": "nisha.verma@example.test", "password": created.temporary_password}
    )

    assert response.status_code == 200
    assert response.data["must_change_password"] is True


def test_nothing_else_works_until_the_password_is_changed(api, created):
    """
    The point of the whole feature. A therapist ordinarily reads their own
    profile and their own documents; with a temporary password they read
    NOTHING, at the API, before touching the SPA.
    """
    api.force_authenticate(user=created.user)

    for path in ("/api/v1/employees/me/", "/api/v1/employee-documents/", "/api/v1/notifications/"):
        response = api.get(path)
        assert response.status_code == 403, path
        assert response.data["error"]["code"] == "password_change_required", path


def test_the_gate_applies_regardless_of_role(
    api, hr_head, designation, org, django_capture_on_commit_callbacks
):
    """An HR Manager on a temporary password is as locked out as anyone."""
    hr_head_employee = hr_head.employee
    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=hr_head,
            first_name="Manav",
            email="manav@example.test",
            role_code="hr_manager",
            department_id=org["departments"][DepartmentKind.HR].pk,
            level_id=org["levels"][Layer.MANAGER].pk,
            location_id=org["location"].pk,
            designation_id=designation.pk,
            reporting_manager_id=hr_head_employee.pk,
            date_of_joining="2026-09-01",
        )
    api.force_authenticate(user=result.user)

    response = api.get("/api/v1/employees/")

    assert response.status_code == 403
    assert response.data["error"]["code"] == "password_change_required"


def test_identity_and_the_change_route_stay_reachable(api, created):
    """What the gate deliberately lets through: who am I, and change it."""
    api.force_authenticate(user=created.user)

    assert api.get("/api/v1/me/").status_code == 200


# --------------------------------------------------------- the change


def test_changing_the_password_requires_the_current_one(api, created):
    api.force_authenticate(user=created.user)

    response = api.post(
        CHANGE, {"current_password": "not-the-temporary-one", "new_password": "Correct-Horse-Battery-9"}
    )

    assert response.status_code == 400
    assert "current_password" in str(response.data)


def test_a_weak_new_password_is_refused(api, created):
    api.force_authenticate(user=created.user)

    response = api.post(
        CHANGE, {"current_password": created.temporary_password, "new_password": "password1234"}
    )

    assert response.status_code == 400


def test_the_temporary_password_stops_working_once_replaced(api, created):
    api.force_authenticate(user=created.user)
    new = "Correct-Horse-Battery-9"

    changed = api.post(CHANGE, {"current_password": created.temporary_password, "new_password": new})
    assert changed.status_code == 200

    created.user.refresh_from_db()
    assert created.user.check_password(created.temporary_password) is False
    assert created.user.check_password(new) is True
    assert created.user.must_change_password is False

    api.force_authenticate(user=None)
    old = api.post(LOGIN, {"email": "nisha.verma@example.test", "password": created.temporary_password})
    # This API reports a wrong password as a validation failure (400), the
    # same as any other bad credential — refused is what matters.
    assert old.status_code in (400, 401)
    fresh = api.post(LOGIN, {"email": "nisha.verma@example.test", "password": new})
    assert fresh.status_code == 200
    assert fresh.data["must_change_password"] is False


def test_after_the_change_the_gate_lifts_and_ordinary_scope_applies(api, created):
    """
    Now the person is an ordinary therapist: they read their own profile and
    are refused a colleague's — the existing matrix, untouched by this feature.
    """
    api.force_authenticate(user=created.user)
    api.post(CHANGE, {"current_password": created.temporary_password, "new_password": "Correct-Horse-Battery-9"})
    created.user.refresh_from_db()
    api.force_authenticate(user=created.user)

    assert api.get("/api/v1/employees/me/").status_code == 200
    # Therapist holds EMPLOYEE at SELF: the directory shows only themselves.
    listing = api.get("/api/v1/employees/")
    assert listing.status_code == 200
    assert {row["employee_code"] for row in listing.data["data"]} == {created.employee.employee_code}
    # And a write they never held stays refused by RBAC, not by the gate.
    refused = api.post("/api/v1/departments/", {"name": "x", "code": "X", "kind": "hr"}, format="json")
    assert refused.status_code == 403
    assert refused.data["error"]["code"] != "password_change_required"


def test_prior_sessions_are_revoked_on_change(api, created):
    """A session opened with the temporary password cannot outlive it."""
    from rest_framework_simplejwt.tokens import RefreshToken
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

    old_refresh = RefreshToken.for_user(created.user)

    passwords.change_password(
        user=created.user,
        current_password=created.temporary_password,
        new_password="Correct-Horse-Battery-9",
    )

    assert BlacklistedToken.objects.filter(token__jti=old_refresh["jti"]).exists()


def test_the_change_is_audited_without_the_secret(created):
    passwords.change_password(
        user=created.user,
        current_password=created.temporary_password,
        new_password="Correct-Horse-Battery-9",
    )

    row = AuditLog.objects.filter(entity_id=str(created.user.pk), after__event="password_changed").first()
    assert row is not None
    assert row.actor_id == created.user.pk
    blob = str(row.after)
    assert "Correct-Horse-Battery-9" not in blob
    assert created.temporary_password not in blob


# ------------------------------------------------- an unrelated account


def test_an_account_without_the_flag_is_unaffected(api, make_user):
    """Everyone already in the system carries on exactly as before."""
    user = make_user("admin")
    assert user.must_change_password is False
    api.force_authenticate(user=user)

    assert api.get("/api/v1/me/").status_code == 200
    assert api.get("/api/v1/departments/").status_code == 200


# --------------------------------------- the reported delivery outcome


def test_a_successful_send_is_reported_as_sent(created):
    """
    Not None, not assumed — the actual outcome. The result object is built
    before the on_commit hook and mutated by it, which only works because
    `transaction.atomic` runs the hooks before handing the value back.
    """
    assert created.welcome_email_sent is True


def test_a_failed_send_is_reported_as_failed(hr_head, director, designation, org, settings,
                                             django_capture_on_commit_callbacks):
    """
    The case that matters: SMTP is misconfigured, the account exists, and the
    caller must NOT be told the credentials went out.
    """
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "127.0.0.1"
    settings.EMAIL_PORT = 1  # nothing listens here

    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=hr_head,
            first_name="Unreachable",
            email="unreachable@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    assert result.welcome_email_sent is False
    # The account is real regardless — creation must not fail on a mail error.
    assert User.objects.filter(email="unreachable@example.test").exists()
    # And the failure is on the record.
    rows = list(AuditLog.objects.filter(entity_id=str(result.user.pk)))
    events = {row.after.get("event") for row in rows}
    assert "welcome_email_failed" in events
    assert "welcome_email_sent" not in events
    # The failure row also names the mailbox that never got the mail — with no
    # personal email on record, that is the login address.
    failed = next(r for r in rows if r.after.get("event") == "welcome_email_failed")
    assert failed.after["recipient"] == "unreachable@example.test"


# ------------------------------------------------- designation is mandatory


def test_creation_without_a_designation_is_refused(hr_head, director, org):
    """
    The rule, at the service — reachable from a command or a shell, so a check
    that lived only in the serializer would not be a check.
    """
    with pytest.raises(ValidationError) as excinfo:
        create_employee(
            actor=hr_head,
            first_name="Titleless",
            email="titleless@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            location_id=org["location"].pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    assert "Designation is required." in str(excinfo.value)
    assert not User.objects.filter(email="titleless@example.test").exists()


def test_the_api_refuses_a_payload_without_a_designation(api, hr_head, director, org):
    """The Add Employee form's own route, with the message HR will read."""
    api.force_authenticate(user=hr_head)

    response = api.post(
        "/api/v1/employees/",
        {
            "first_name": "Titleless",
            "email": "titleless.api@example.test",
            "role_code": "therapist",
            "department_id": str(org["departments"][DepartmentKind.MEDICAL].pk),
            "level_id": str(org["levels"][Layer.STAFF].pk),
            "reporting_manager_id": str(director.pk),
            "date_of_joining": "2026-09-01",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "Designation is required." in str(response.data)
    assert not User.objects.filter(email="titleless.api@example.test").exists()


def test_an_explicit_null_designation_is_refused_too(api, hr_head, director, org):
    """Sending null must not slip past a `required` check."""
    api.force_authenticate(user=hr_head)

    response = api.post(
        "/api/v1/employees/",
        {
            "first_name": "Nulled",
            "email": "nulled@example.test",
            "role_code": "therapist",
            "department_id": str(org["departments"][DepartmentKind.MEDICAL].pk),
            "designation_id": None,
            "level_id": str(org["levels"][Layer.STAFF].pk),
            "reporting_manager_id": str(director.pk),
            "date_of_joining": "2026-09-01",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "Designation is required." in str(response.data)


def test_a_designation_is_saved_and_reaches_the_email(
    api, hr_head, director, org, django_capture_on_commit_callbacks
):
    """Saved on the record, and printed in the message — the whole point."""
    from apps.employees.models import Employee
    from apps.organization.models import Designation

    title = Designation.objects.create(title="Consultant Physiotherapist")
    api.force_authenticate(user=hr_head)

    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/employees/",
            {
                "first_name": "Titled",
                "last_name": "Joiner",
                "email": "titled.joiner@example.test",
                "personal_email": "titled.personal@example.test",
                "role_code": "therapist",
                "department_id": str(org["departments"][DepartmentKind.MEDICAL].pk),
                "designation_id": str(title.pk),
                "level_id": str(org["levels"][Layer.STAFF].pk),
                "reporting_manager_id": str(director.pk),
                "date_of_joining": "2026-09-01",
            },
            format="json",
        )

    assert response.status_code == 201, response.data

    employee = Employee.objects.get(user__email="titled.joiner@example.test")
    assert employee.designation_id == title.pk

    body = mail.outbox[-1].body
    assert "Position: Consultant Physiotherapist" in body
    html = mail.outbox[-1].alternatives[0][0]
    assert "Consultant Physiotherapist" in html


def test_hiring_a_candidate_still_works_without_one(hr_head, director, org):
    """
    The exemption, and why it exists: a job opening may carry no designation,
    and refusing at conversion would strand a candidate who already accepted
    an offer. This is the ONE caller allowed past the rule.
    """
    result = create_employee(
        actor=hr_head,
        first_name="Converted",
        email="converted@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        location_id=org["location"].pk,
        reporting_manager_id=director.pk,
        date_of_joining="2026-09-01",
        require_designation=False,
    )

    assert result.employee.designation_id is None


def test_the_welcome_email_sends_from_hr_mailbox_when_one_is_configured(
    hr_head, director, designation, org, django_capture_on_commit_callbacks, settings
):
    """
    The office sends the welcome email from HR's own mailbox, distinct from
    the recruitment sender candidates correspond with. Configured entirely by
    environment; unset (the default everywhere else in this file) keeps the
    primary sender — pinned by the other tests still asserting one outbox
    message from DEFAULT_FROM_EMAIL's connection.
    """
    from unittest.mock import patch

    from django.core import mail as django_mail

    settings.HR_EMAIL_HOST_USER = "hr.desk@example.test"
    settings.HR_EMAIL_HOST_PASSWORD = "app-password"
    settings.HR_FROM_EMAIL = "Test Clinic HR <hr.desk@example.test>"

    with patch(
        "django.core.mail.get_connection", wraps=django_mail.get_connection
    ) as connection_spy, django_capture_on_commit_callbacks(execute=True):
        create_employee(
            actor=hr_head,
            first_name="Second",
            last_name="Joiner",
            email="second.joiner@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            level_id=org["levels"][Layer.STAFF].pk,
            designation_id=designation.pk,
            reporting_manager_id=director.pk,
            date_of_joining="2026-09-01",
        )

    message = mail.outbox[-1]
    assert message.from_email == "Test Clinic HR <hr.desk@example.test>"
    # The dedicated connection was built with HR's credentials, not the primary's.
    hr_calls = [
        c for c in connection_spy.call_args_list
        if c.kwargs.get("username") == "hr.desk@example.test"
    ]
    assert hr_calls, "welcome email did not open HR's own connection"
    assert hr_calls[0].kwargs["password"] == "app-password"
