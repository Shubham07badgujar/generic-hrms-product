"""
Reporting lines: same level or higher, and never a loop.

Seniority is not what grants permission here — the ROLE is — so a peer
manager gains no authority over their report. That is what makes same-layer
reporting safe to allow, and it is why the rule that actually matters is the
one against cycles.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.employees.models import Employee
from apps.employees.services.creation import create_employee
from apps.employees.services.hierarchy import (
    HierarchyError,
    assert_reporting_manager_is_valid,
    eligible_reporting_managers,
    set_reporting_manager,
)
from core.access.catalog import DepartmentKind
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

EMPLOYEES = "/api/v1/employees/"


@pytest.fixture
def hr_boss(db, org, roles):
    """An existing HR Head — the manager the reported bug could not select."""
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="hrboss@rm.test", password="test-password-12345")
    UserRole.objects.create(user=user, role=roles["hr_head"])
    bind_membership(user)
    return Employee.objects.create(
        employee_code="EMP07001", first_name="Asha", last_name="Boss", user=user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )


@pytest.fixture
def med_director(db, org, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="meddir@rm.test", password="test-password-12345")
    UserRole.objects.create(user=user, role=roles["medical_director"])
    bind_membership(user)
    return Employee.objects.create(
        employee_code="EMP07002", first_name="Vikram", last_name="Rao", user=user,
        department=org["departments"][DepartmentKind.MEDICAL], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )


@pytest.fixture
def staffer(db, org, roles, hr_boss):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="staffer@rm.test", password="test-password-12345")
    UserRole.objects.create(user=user, role=roles["employee"])
    bind_membership(user)
    return Employee.objects.create(
        employee_code="EMP07003", first_name="Sam", last_name="Junior", user=user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 6, 1), reporting_manager=hr_boss,
    )


# ------------------------------------------------- the reported bug, pinned


def test_a_new_hr_head_may_report_to_another_hr_head(org, roles, hr_boss, make_user):
    """
    The exact case from the report: creating an HR Head and naming another
    HR Head as their manager used to be refused for being 'not more senior'.
    """
    creator = make_user("admin")

    result = create_employee(
        actor=creator, first_name="Neha", last_name="Second",
        email="neha.second@rm.test", personal_email="neha.second@personal.test",
        role_code="hr_head",
        department_id=org["departments"][DepartmentKind.HR].pk,
        designation_id=org["any_designation"].pk,
        location_id=org["location"].pk,
        reporting_manager_id=hr_boss.pk,
        date_of_joining=dt.date(2026, 9, 1),
    )

    assert result.employee.reporting_manager_id == hr_boss.pk


def test_an_hr_head_may_report_to_a_medical_director(org, roles, med_director, make_user):
    """Same layer, different department — a cross-functional peer line."""
    result = create_employee(
        actor=make_user("admin"), first_name="Priya", last_name="Cross",
        email="priya.cross@rm.test", personal_email="priya.cross@personal.test",
        role_code="hr_head",
        department_id=org["departments"][DepartmentKind.HR].pk,
        designation_id=org["any_designation"].pk,
        location_id=org["location"].pk,
        reporting_manager_id=med_director.pk,
        date_of_joining=dt.date(2026, 9, 1),
    )

    assert result.employee.reporting_manager_id == med_director.pk


def test_a_more_senior_manager_is_still_accepted(org, roles, hr_boss, make_user):
    """Higher-level reporting keeps working exactly as before."""
    result = create_employee(
        actor=make_user("admin"), first_name="Ravi", last_name="Junior",
        email="ravi.junior@rm.test", personal_email="ravi.junior@personal.test",
        role_code="hr_manager",
        department_id=org["departments"][DepartmentKind.HR].pk,
        designation_id=org["any_designation"].pk,
        location_id=org["location"].pk,
        reporting_manager_id=hr_boss.pk,
        date_of_joining=dt.date(2026, 9, 1),
    )

    assert result.employee.reporting_manager_id == hr_boss.pk


def test_a_junior_manager_is_still_refused(org, roles, staffer, make_user):
    """
    The one direction that stays closed: a line running downward would invert
    every approval the org chart exists to route.
    """
    with pytest.raises(HierarchyError) as refused:
        create_employee(
            actor=make_user("admin"), first_name="Upside", last_name="Down",
            email="upside.down@rm.test", personal_email="upside.down@personal.test",
            role_code="hr_head",
            department_id=org["departments"][DepartmentKind.HR].pk,
            designation_id=org["any_designation"].pk,
            location_id=org["location"].pk,
            reporting_manager_id=staffer.pk,
            date_of_joining=dt.date(2026, 9, 1),
        )

    assert "more junior" in str(refused.value)


# ------------------------------------------------------ self and cycles


def test_nobody_reports_to_themselves(staffer, roles):
    role = roles["employee"]

    with pytest.raises(HierarchyError) as refused:
        assert_reporting_manager_is_valid(
            manager=staffer, role=role, department=staffer.department, employee=staffer
        )

    assert "cannot report to themselves" in str(refused.value)


def test_a_two_person_loop_is_refused(hr_boss, staffer, roles):
    """
    staffer already reports to hr_boss. Pointing hr_boss at staffer would
    close the loop — and now that peers may manage peers, this is the rule
    doing the real work.
    """
    with pytest.raises(HierarchyError) as refused:
        assert_reporting_manager_is_valid(
            manager=staffer, role=roles["hr_head"],
            department=hr_boss.department, employee=hr_boss,
        )

    # A junior manager trips the layer rule first; the message names it.
    assert "more junior" in str(refused.value) or "reporting loop" in str(refused.value)


def test_a_peer_loop_is_refused(org, roles, hr_boss):
    """Two same-layer heads cannot point at each other."""
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="peer@rm.test", password="test-password-12345")
    UserRole.objects.create(user=user, role=roles["hr_head"])
    bind_membership(user)
    peer = Employee.objects.create(
        employee_code="EMP07004", first_name="Peer", last_name="Head", user=user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1), reporting_manager=hr_boss,
    )

    # peer -> hr_boss already. Now hr_boss -> peer would close it.
    with pytest.raises(HierarchyError) as refused:
        assert_reporting_manager_is_valid(
            manager=peer, role=roles["hr_head"],
            department=hr_boss.department, employee=hr_boss,
        )

    assert "reporting loop" in str(refused.value)


def test_a_longer_loop_is_refused(org, roles, hr_boss):
    """A → B → C → A, caught by walking the whole chain."""
    from apps.accounts.models import User, UserRole

    chain = [hr_boss]
    for index in (5, 6):
        user = User.objects.create_user(
            email=f"chain{index}@rm.test", password="test-password-12345"
        )
        UserRole.objects.create(user=user, role=roles["hr_head"])
        bind_membership(user)
        chain.append(
            Employee.objects.create(
                employee_code=f"EMP0700{index}", first_name=f"Chain{index}", last_name="Head",
                user=user, department=org["departments"][DepartmentKind.HR],
                location=org["location"], date_of_joining=dt.date(2025, 1, 1),
                reporting_manager=chain[-1],
            )
        )

    with pytest.raises(HierarchyError) as refused:
        assert_reporting_manager_is_valid(
            manager=chain[-1], role=roles["hr_head"],
            department=hr_boss.department, employee=hr_boss,
        )

    assert "reporting loop" in str(refused.value)


# ------------------------------------------- the dropdown matches the rule


def test_the_eligible_list_offers_peers_and_seniors_but_not_juniors(
    org, roles, hr_boss, med_director, staffer
):
    offered = set(
        eligible_reporting_managers(role=roles["hr_head"]).values_list(
            "employee_code", flat=True
        )
    )

    assert hr_boss.employee_code in offered        # same layer
    assert med_director.employee_code in offered   # same layer, other department
    assert staffer.employee_code not in offered    # junior


def test_the_eligible_list_hides_the_person_and_their_own_reports(
    org, roles, hr_boss, staffer
):
    """Offering someone's own reportee is offering them a loop."""
    offered = set(
        eligible_reporting_managers(role=roles["hr_head"], employee=hr_boss).values_list(
            "employee_code", flat=True
        )
    )

    assert hr_boss.employee_code not in offered
    assert staffer.employee_code not in offered


# ---------------------------------------------------------- editing a line


def test_hr_repoints_a_reporting_line_over_the_api(api, org, roles, hr_boss, staffer, make_user):
    """The org chart has to be editable — people move."""
    from apps.accounts.models import User, UserRole

    hr_user = User.objects.create_user(email="hrhead@rm.test", password="test-password-12345")
    UserRole.objects.create(user=hr_user, role=roles["hr_head"])
    bind_membership(hr_user)
    Employee.objects.create(
        employee_code="EMP07009", first_name="Hr", last_name="Head", user=hr_user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )
    api.force_authenticate(user=hr_user)

    new_boss = Employee.objects.create(
        employee_code="EMP07010", first_name="New", last_name="Boss",
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1), user=User.objects.create_user(
            email="newboss@rm.test", password="test-password-12345"
        ),
    )
    UserRole.objects.create(user=new_boss.user, role=roles["hr_manager"])
    bind_membership(new_boss.user)

    response = api.patch(
        f"{EMPLOYEES}{staffer.pk}/reporting-manager/",
        {"reporting_manager": str(new_boss.pk), "reason": "Team move"},
        format="json",
    )

    assert response.status_code == 200, response.content
    staffer.refresh_from_db()
    assert staffer.reporting_manager_id == new_boss.pk


def test_the_edit_refuses_a_loop_over_the_api(api, org, roles, hr_boss, staffer, make_user):
    from apps.accounts.models import User, UserRole

    hr_user = User.objects.create_user(email="hrhead2@rm.test", password="test-password-12345")
    UserRole.objects.create(user=hr_user, role=roles["hr_head"])
    bind_membership(hr_user)
    Employee.objects.create(
        employee_code="EMP07011", first_name="Hr", last_name="Head2", user=hr_user,
        department=org["departments"][DepartmentKind.HR], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )
    api.force_authenticate(user=hr_user)

    # staffer reports to hr_boss; sending hr_boss under staffer closes the loop.
    response = api.patch(
        f"{EMPLOYEES}{hr_boss.pk}/reporting-manager/",
        {"reporting_manager": str(staffer.pk)},
        format="json",
    )

    assert response.status_code == 400
    hr_boss.refresh_from_db()
    assert hr_boss.reporting_manager_id is None


def test_editing_a_reporting_line_is_not_open_to_a_team_lead(api, roles, staffer, make_user):
    """A lead must not be able to quietly redirect who approves someone."""
    api.force_authenticate(user=make_user("operations_manager"))

    response = api.patch(
        f"{EMPLOYEES}{staffer.pk}/reporting-manager/",
        {"reporting_manager": None},
        format="json",
    )

    assert response.status_code in (403, 404)


def test_the_change_is_audited(org, roles, hr_boss, med_director, staffer, make_user):
    """Who moved the line, and where it moved from — both recorded."""
    from apps.audit.models import AuditLog

    set_reporting_manager(
        employee=staffer, manager=med_director, actor=make_user("admin"),
        reason="Moved under the clinical side",
    )

    # The model's own save signal writes an audit row for the same employee at
    # the same instant, so select THIS event by its reason rather than trusting
    # the ordering of two rows that share a timestamp.
    entry = AuditLog.objects.filter(
        entity_id=str(staffer.pk), reason__icontains="clinical side"
    ).first()
    assert entry is not None
    assert entry.before["reporting_manager"] == hr_boss.employee_code
    assert entry.after["reporting_manager"] == med_director.employee_code


def test_a_staff_member_may_not_be_left_without_a_manager(staffer, make_user):
    """Clearing the line is only open to those allowed none — heads and above."""
    with pytest.raises(HierarchyError) as refused:
        set_reporting_manager(employee=staffer, manager=None, actor=make_user("admin"))

    assert "must have a reporting manager" in str(refused.value)
