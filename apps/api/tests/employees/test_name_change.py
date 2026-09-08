"""
Changing the name on an employee record.

Two callers, one rule set: HR correcting anyone's spelling, and the employee
themselves after a marriage or legal change. The interesting part is not that
both can — it is that VIEW is wider than EDIT for several roles, so "can reach
the page" must not become "can rename the person".
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.employees.models import Employee
from apps.employees.services.profile import rename_employee
from core.access.catalog import DepartmentKind
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

EMPLOYEES = "/api/v1/employees/"
PASSWORD = "test-password-12345"


def _person(org, roles, code, first, last, role_code, email, department=None):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email=email, password=PASSWORD)
    UserRole.objects.create(user=user, role=roles[role_code])
    bind_membership(user)
    return Employee.objects.create(
        employee_code=code, first_name=first, last_name=last, user=user,
        department=department or org["departments"][DepartmentKind.MEDICAL],
        location=org["location"], date_of_joining=dt.date(2025, 1, 1),
    )


@pytest.fixture
def hr_head(db, org, roles):
    return _person(org, roles, "EMP05000", "Hema", "Rao", "hr_head", "hr@name.test",
                   org["departments"][DepartmentKind.HR])


@pytest.fixture
def worker(db, org, roles):
    return _person(org, roles, "EMP05001", "Priya", "Sharma", "therapist", "priya@name.test")


@pytest.fixture
def director(db, org, roles):
    """Sees the whole medical department, but may only edit themselves."""
    return _person(org, roles, "EMP05002", "Vikram", "Rao", "medical_director",
                   "md@name.test")


# --------------------------------------------------------------- HR renames


def test_hr_corrects_a_misspelled_name(api, hr_head, worker):
    api.force_authenticate(user=hr_head.user)

    response = api.patch(
        f"{EMPLOYEES}{worker.pk}/name/",
        {"first_name": "Priyanka", "last_name": "Sharma", "reason": "Spelling at data entry"},
        format="json",
    )

    assert response.status_code == 200, response.content
    worker.refresh_from_db()
    assert worker.full_name == "Priyanka Sharma"


def test_the_derived_full_name_follows_immediately(api, hr_head, worker):
    """full_name is computed, so nothing downstream needs updating."""
    api.force_authenticate(user=hr_head.user)

    api.patch(f"{EMPLOYEES}{worker.pk}/name/",
              {"first_name": "Priya", "middle_name": "Anand", "last_name": "Desai"},
              format="json")

    worker.refresh_from_db()
    assert worker.full_name == "Priya Anand Desai"
    listed = api.get(EMPLOYEES, {"search": "Desai"}).json()["data"]
    assert any(row["full_name"] == "Priya Anand Desai" for row in listed)


# ------------------------------------------------- the employee's own name


def test_an_employee_updates_their_own_name(api, worker):
    """The person, not the office, is the authority on their own name."""
    api.force_authenticate(user=worker.user)

    response = api.patch(
        f"{EMPLOYEES}{worker.pk}/name/",
        {"first_name": "Priya", "last_name": "Iyer", "reason": "Married name"},
        format="json",
    )

    assert response.status_code == 200, response.content
    worker.refresh_from_db()
    assert worker.full_name == "Priya Iyer"


def test_an_employee_may_drop_a_middle_name(api, worker):
    worker.middle_name = "Anand"
    worker.save(update_fields=["middle_name"])
    api.force_authenticate(user=worker.user)

    api.patch(f"{EMPLOYEES}{worker.pk}/name/", {"middle_name": ""}, format="json")

    worker.refresh_from_db()
    assert worker.middle_name == ""
    assert worker.full_name == "Priya Sharma"


def test_an_employee_cannot_rename_a_colleague(api, worker, org, roles):
    other = _person(org, roles, "EMP05003", "Someone", "Else", "therapist", "other@name.test")
    api.force_authenticate(user=worker.user)

    response = api.patch(f"{EMPLOYEES}{other.pk}/name/", {"first_name": "Hacked"},
                         format="json")

    assert response.status_code in (403, 404)
    other.refresh_from_db()
    assert other.first_name == "Someone"


def test_seeing_a_department_does_not_mean_renaming_it(api, director, worker):
    """
    The guard that matters. A Medical Director's VIEW covers the whole medical
    department while their EDIT is self-only — and a custom action scopes its
    lookup by VIEW. Without an explicit EDIT check they could rename anyone
    they can see.
    """
    api.force_authenticate(user=director.user)
    # They really can see this person…
    assert api.get(f"{EMPLOYEES}{worker.pk}/").status_code == 200

    # …and really cannot rename them.
    response = api.patch(f"{EMPLOYEES}{worker.pk}/name/", {"first_name": "Renamed"},
                         format="json")

    assert response.status_code == 403
    worker.refresh_from_db()
    assert worker.first_name == "Priya"


def test_the_director_may_still_rename_themselves(api, director):
    api.force_authenticate(user=director.user)

    response = api.patch(f"{EMPLOYEES}{director.pk}/name/", {"first_name": "Vikrant"},
                         format="json")

    assert response.status_code == 200, response.content
    director.refresh_from_db()
    assert director.first_name == "Vikrant"


# ------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"first_name": ""}, "cannot be empty"),
        ({"first_name": "   "}, "cannot be empty"),
        ({"first_name": "Priya2"}, "cannot contain digits"),
        ({"first_name": "<script>"}, "cannot contain digits or symbols"),
        ({"last_name": "A" * 101}, "longer than 100"),
    ],
)
def test_a_name_must_look_like_a_name(api, hr_head, worker, payload, expected):
    api.force_authenticate(user=hr_head.user)

    response = api.patch(f"{EMPLOYEES}{worker.pk}/name/", payload, format="json")

    assert response.status_code == 400
    assert expected in response.content.decode()
    worker.refresh_from_db()
    assert worker.first_name == "Priya"


@pytest.mark.parametrize(
    "name",
    ["O'Brien", "Smith-Jones", "J. Patel", "अनन्या", "María", "van der Berg"],
)
def test_real_names_are_accepted(api, hr_head, worker, name):
    """Apostrophes, hyphens, initials and non-Latin scripts are all names."""
    api.force_authenticate(user=hr_head.user)

    response = api.patch(f"{EMPLOYEES}{worker.pk}/name/", {"last_name": name},
                         format="json")

    assert response.status_code == 200, response.content
    worker.refresh_from_db()
    assert worker.last_name == name


def test_extra_whitespace_is_tidied(api, hr_head, worker):
    api.force_authenticate(user=hr_head.user)

    api.patch(f"{EMPLOYEES}{worker.pk}/name/",
              {"first_name": "  Priya   Anne  "}, format="json")

    worker.refresh_from_db()
    assert worker.first_name == "Priya Anne"


def test_an_empty_request_is_refused(api, hr_head, worker):
    api.force_authenticate(user=hr_head.user)

    response = api.patch(f"{EMPLOYEES}{worker.pk}/name/", {}, format="json")

    assert response.status_code == 400


# ------------------------------------------------------------------ audit


def test_the_change_is_audited_with_both_names(hr_head, worker):
    from apps.audit.models import AuditLog

    rename_employee(employee=worker, first_name="Priya", last_name="Iyer",
                    actor=hr_head.user, reason="Married name")

    entry = AuditLog.objects.filter(
        entity_id=str(worker.pk), reason__icontains="married name"
    ).first()
    assert entry is not None
    assert entry.before["full_name"] == "Priya Sharma"
    assert entry.after["full_name"] == "Priya Iyer"


def test_renaming_to_the_same_name_writes_nothing(hr_head, worker):
    from apps.audit.models import AuditLog

    before = AuditLog.objects.filter(entity_id=str(worker.pk)).count()

    rename_employee(employee=worker, first_name="Priya", last_name="Sharma",
                    actor=hr_head.user)

    assert AuditLog.objects.filter(entity_id=str(worker.pk)).count() == before


def test_the_employee_code_never_changes(api, hr_head, worker):
    """Identity is the code, not the name — renaming must not disturb it."""
    api.force_authenticate(user=hr_head.user)
    code = worker.employee_code

    api.patch(f"{EMPLOYEES}{worker.pk}/name/", {"first_name": "Completely",
                                                "last_name": "Different"}, format="json")

    worker.refresh_from_db()
    assert worker.employee_code == code
    assert worker.user.email == "priya@name.test"  # login is untouched too
