"""
Employee API.

The point of these tests is that the hierarchy is enforced over HTTP — not
merely in the service a UI is trusted to call correctly.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.access.catalog import DepartmentKind, Layer

pytestmark = pytest.mark.django_db

EMPLOYEES = "/api/v1/employees/"
PASSWORD = "test-password-12345"


def _auth(api, user):
    token = api.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def _payload(org, manager, **overrides):
    return {
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya.api@example.test",
        # Both addresses are required now: the company email is the login,
        # the personal one is where the credential email goes.
        "personal_email": "priya.personal@example.test",
        "role_code": "therapist",
        "department_id": str(org["departments"][DepartmentKind.MEDICAL].pk),
        # Required by the API since designations were made mandatory. Every
        # test below that overrides something else still needs a valid one,
        # or it would be refused for the designation rather than the thing
        # it set out to prove.
        "designation_id": str(org["designation"].pk),
        "reporting_manager_id": str(manager.pk),
        "date_of_joining": "2026-01-15",
        **overrides,
    }


# ================================================================ create


def test_hr_head_can_create_an_employee_over_http(api, hr_head, org, medical_director):
    _auth(api, hr_head)

    response = api.post(EMPLOYEES, _payload(org, medical_director), format="json")

    assert response.status_code == 201, response.data
    assert response.data["role"] == "therapist"
    assert response.data["employee"]["employee_code"]
    assert response.data["user"]["email"] == "priya.api@example.test"


def test_unauthorised_role_cannot_create_over_http(api, make_user, org, medical_director):
    """
    A therapist holds no USER/CREATE. RBACPermission refuses before the view
    body runs, so the service is never reached.
    """
    therapist = make_user("therapist")
    _auth(api, therapist)

    response = api.post(EMPLOYEES, _payload(org, medical_director), format="json")

    assert response.status_code == 403


def test_anonymous_cannot_create(api, org, medical_director):
    assert api.post(EMPLOYEES, _payload(org, medical_director), format="json").status_code == 401


def test_invalid_role_department_pairing_is_refused_over_http(
    api, hr_head, org, medical_director
):
    _auth(api, hr_head)

    response = api.post(
        EMPLOYEES,
        _payload(
            org,
            medical_director,
            role_code="hr_head",
            department_id=str(org["departments"][DepartmentKind.MEDICAL].pk),
        ),
        format="json",
    )

    assert response.status_code == 400
    assert "function" in str(response.data).lower()


def test_system_level_role_is_refused_over_http(api, hr_head, org, medical_director):
    _auth(api, hr_head)

    response = api.post(
        EMPLOYEES,
        _payload(org, medical_director, role_code="admin"),
        format="json",
    )

    assert response.status_code == 400
    assert "system-level" in str(response.data).lower()


def test_failed_creation_leaves_no_user(api, hr_head, org, medical_director):
    from apps.accounts.models import User

    _auth(api, hr_head)
    before = User.objects.count()

    api.post(
        EMPLOYEES,
        _payload(org, medical_director, role_code="hr_head"),  # wrong department
        format="json",
    )

    assert User.objects.count() == before


def test_missing_required_field_is_a_400(api, hr_head, org, medical_director):
    _auth(api, hr_head)
    payload = _payload(org, medical_director)
    del payload["date_of_joining"]

    response = api.post(EMPLOYEES, payload, format="json")

    assert response.status_code == 400
    assert "date_of_joining" in str(response.data)


# ================================================================== read


def test_list_is_scoped_to_the_callers_department(api, hr_head, org, medical_director, roles):
    """
    A Medical Director sees medical staff and not finance staff — without the
    view writing a single filter.
    """
    from apps.employees.services.creation import create_employee

    create_employee(
        actor=hr_head,
        first_name="Medical",
        email="med.staff@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=medical_director.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )
    create_employee(
        actor=hr_head,
        first_name="Finance",
        email="fin.staff@example.test",
        role_code="employee",
        department_id=org["departments"][DepartmentKind.FINANCE].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=medical_director.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )

    _auth(api, medical_director.user)
    names = {row["full_name"] for row in api.get(EMPLOYEES).data["data"]}

    assert "Medical" in " ".join(names)
    assert "Finance" not in " ".join(names), (
        "A department head must not see another department's staff."
    )


def test_self_scoped_employee_sees_only_themselves(
    api, hr_head, org, medical_director, first_login_done
):
    from apps.employees.services.creation import create_employee

    result = create_employee(
        actor=hr_head,
        first_name="Solo",
        email="solo@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=medical_director.pk,
        date_of_joining=dt.date(2026, 1, 1),
        # Needed to sign in: without it the account is created with an
        # unusable password, which is the safer default for real hires.
        temporary_password=PASSWORD,
    )
    # This test signs in AS the new hire; model a completed first login.
    first_login_done(result.user)

    _auth(api, result.user)
    rows = api.get(EMPLOYEES).data["data"]

    assert len(rows) == 1
    assert rows[0]["employee_code"] == result.employee.employee_code


def test_me_returns_the_callers_record(api, hr_head):
    _auth(api, hr_head)

    response = api.get(f"{EMPLOYEES}me/")

    assert response.status_code == 200
    assert response.data["work_email"] or response.data["full_name"]


def test_me_is_404_for_a_system_level_role(api, make_user):
    """CEO and Admin have no Employee record; the message must say so."""
    admin = make_user("admin")
    _auth(api, admin)

    response = api.get(f"{EMPLOYEES}me/")

    assert response.status_code == 404
    assert response.data["error"]["code"] == "no_employee_record"


def test_detail_exposes_only_masked_pii(api, hr_head, org, medical_director):
    from apps.employees.services.creation import create_employee

    result = create_employee(
        actor=hr_head,
        first_name="Masked",
        email="masked@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=medical_director.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )
    result.employee.pan = "ABCDE1234F"
    result.employee.aadhaar = "123412341234"
    result.employee.save(update_fields=["pan", "aadhaar"])

    _auth(api, hr_head)
    response = api.get(f"{EMPLOYEES}{result.employee.pk}/")

    body = str(response.data)
    assert "ABCDE1234F" not in body, "Unmasked PAN must never appear in a profile response."
    assert "123412341234" not in body, "Unmasked Aadhaar must never appear."
    assert "X" in response.data["aadhaar"]


# ============================================= audit attribution over JWT


def test_jwt_authenticated_creation_is_attributed_correctly(
    api, hr_head, org, medical_director
):
    """
    THE ATTRIBUTION GUARANTEE.

    JWT is resolved inside DRF dispatch, after middleware. Without
    ContextBindingJWTAuthentication the audit ContextVar would still hold
    AnonymousUser and every API write would be recorded with no actor.
    """
    from apps.audit.models import AuditLog

    _auth(api, hr_head)
    response = api.post(EMPLOYEES, _payload(org, medical_director), format="json")
    employee_id = response.data["employee"]["id"]

    entry = AuditLog.objects.filter(
        entity_type="employees.Employee", entity_id=str(employee_id)
    ).first()

    assert entry is not None
    assert entry.actor_id == hr_head.pk, "The JWT user must be the recorded actor."
    assert entry.actor_email == hr_head.email


def test_created_by_is_stamped_from_the_jwt_user(api, hr_head, org, medical_director):
    from apps.employees.models import Employee

    _auth(api, hr_head)
    response = api.post(EMPLOYEES, _payload(org, medical_director), format="json")

    employee = Employee.objects.get(pk=response.data["employee"]["id"])
    assert employee.created_by_id == hr_head.pk
    # `User` extends AbstractBaseUser, not BaseModel, so it carries no
    # created_by column. Its provenance lives in the audit log instead —
    # asserted by the preceding test.
