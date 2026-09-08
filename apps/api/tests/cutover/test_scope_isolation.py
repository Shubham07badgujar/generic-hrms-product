"""
Cutover gate 5: data and scope isolation.

The named requirements, each asserted directly rather than inferred from the
matrix. The matrix says what SHOULD happen; these check what does — across
every scoped resource at once, so a resource that was wired up incorrectly
shows here even if its grant is right.
"""

from __future__ import annotations

import pytest

from core.access import Action, Resource, Scope, can, scope_queryset

pytestmark = pytest.mark.django_db


def _visible_employees(user):
    from apps.employees.models import Employee

    return set(
        scope_queryset(
            Employee.objects.all(), user, resource=Resource.EMPLOYEE, action=Action.VIEW
        ).values_list("employee_code", flat=True)
    )


# ===================================================== department isolation


def test_medical_cannot_reach_operations_data(everyone, staff):
    """The explicit requirement, over the employee resource."""
    visible = _visible_employees(everyone["medical_director"])
    operations = {
        staff[role].employee_code
        for role in ("operational_head", "operations_manager", "cre", "office_boy")
    }

    assert visible.isdisjoint(operations), (
        f"Medical Director sees Operations staff: {sorted(visible & operations)}"
    )
    assert staff["therapist"].employee_code in visible, "…and cannot see their own people."


def test_operations_cannot_reach_medical_data(everyone, staff):
    visible = _visible_employees(everyone["operational_head"])
    medical = {
        staff[role].employee_code
        for role in ("medical_director", "senior_doctor", "clinic_doctor", "therapist")
    }

    assert visible.isdisjoint(medical), (
        f"Operational Head sees Medical staff: {sorted(visible & medical)}"
    )
    assert staff["cre"].employee_code in visible


def test_department_isolation_holds_across_every_scoped_resource(everyone, staff):
    """
    Not just employees.

    A resource whose registry path was wired to the wrong relation would leak
    even though its grant is correct, and only a sweep like this finds it.
    """
    from django.apps import apps as django_apps

    from core.access.registry import RESOURCE_SPECS

    medical = everyone["medical_director"]
    operations_department = staff["cre"].department_id

    leaks = []
    for resource, spec in RESOURCE_SPECS.items():
        if not spec.person_scoped:
            continue
        if can(medical, resource, Action.VIEW) != Scope.DEPARTMENT:
            continue

        try:
            model = django_apps.get_model(spec.model_label)
        except LookupError:
            continue

        rows = scope_queryset(
            model.objects.all(), medical, resource=resource, action=Action.VIEW
        )

        path = spec.employee_path
        lookup = (
            "department_id" if path == "" else f"{path}__department_id"
        )
        try:
            intruders = rows.filter(**{lookup: operations_department}).count()
        except Exception:
            # A resource whose path does not support this lookup is reported
            # rather than skipped silently — it means the spec is suspect.
            leaks.append(f"{resource}: could not verify via '{lookup}'")
            continue

        if intruders:
            leaks.append(f"{resource}: {intruders} Operations row(s) visible to Medical")

    assert not leaks, "Cross-department leakage:\n  " + "\n  ".join(leaks)


# ========================================================== team isolation


def test_a_manager_sees_their_reporting_tree_and_not_the_department(everyone, staff):
    """
    TEAM is narrower than DEPARTMENT, and the difference must be real.

    A manager whose TEAM scope silently behaved like DEPARTMENT would be a
    quiet privilege escalation for every manager in the organisation.
    """
    visible = _visible_employees(everyone["senior_doctor"])

    assert staff["senior_doctor"].employee_code in visible
    assert staff["therapist"].employee_code in visible       # reports to them
    assert staff["clinic_doctor"].employee_code in visible   # reports to them
    # Their own department head is ABOVE them, not below.
    assert staff["medical_director"].employee_code not in visible


def test_a_manager_cannot_reach_a_peer_managers_team(everyone, staff):
    medical_manager = _visible_employees(everyone["senior_doctor"])
    operations_team = {staff["cre"].employee_code, staff["office_boy"].employee_code}

    assert medical_manager.isdisjoint(operations_team)


# ========================================================== self isolation


def test_an_employee_sees_only_themselves(everyone, staff):
    for role in ("therapist", "office_boy", "employee", "cre"):
        visible = _visible_employees(everyone[role])
        assert visible == {staff[role].employee_code}, (
            f"{role} sees {sorted(visible)} rather than only themselves."
        )


def test_an_employee_cannot_reach_another_employees_private_records(everyone, staff):
    """Documents, payslips and salary — the three that matter most."""
    from django.apps import apps as django_apps

    checks = [
        (Resource.EMPLOYEE_DOCUMENT, "employees.EmployeeDocument", "employee"),
        (Resource.PAYSLIP, "payroll.Payslip", "employee"),
        (Resource.SALARY, "payroll.SalaryStructure", "employee"),
    ]

    viewer = everyone["therapist"]
    others = [staff[r].pk for r in ("hr_head", "cre", "office_boy")]

    leaks = []
    for resource, label, path in checks:
        model = django_apps.get_model(label)
        rows = scope_queryset(
            model.objects.all(), viewer, resource=resource, action=Action.VIEW
        )
        intruders = rows.filter(**{f"{path}_id__in": others}).count()
        if intruders:
            leaks.append(f"{resource}: {intruders} row(s) belonging to others")

    assert not leaks, "An employee reached private records:\n  " + "\n  ".join(leaks)


# ===================================================== the system principals


def test_ceo_sees_the_whole_organisation(everyone, staff):
    visible = _visible_employees(everyone["ceo"])
    everyone_code = {employee.employee_code for employee in staff.values()}

    assert everyone_code <= visible, (
        f"CEO cannot see: {sorted(everyone_code - visible)}"
    )


def test_ceo_can_view_but_not_modify(everyone, staff):
    """Both halves in one assertion, because either alone is misleading."""
    from apps.employees.models import Employee

    ceo = everyone["ceo"]

    assert can(ceo, Resource.EMPLOYEE, Action.VIEW) == Scope.ALL
    assert not can(ceo, Resource.EMPLOYEE, Action.EDIT)
    assert not can(ceo, Resource.EMPLOYEE, Action.CREATE)
    assert not can(ceo, Resource.EMPLOYEE, Action.DELETE)

    # And the scoped queryset genuinely returns rows — a read-only principal
    # who can read nothing would pass every "cannot write" test trivially.
    assert scope_queryset(
        Employee.objects.all(), ceo, resource=Resource.EMPLOYEE, action=Action.VIEW
    ).exists()


def test_admin_has_organisation_wide_management(everyone, staff):
    admin = everyone["admin"]
    visible = _visible_employees(admin)

    assert {employee.employee_code for employee in staff.values()} <= visible
    assert can(admin, Resource.EMPLOYEE, Action.EDIT) == Scope.ALL
    assert can(admin, Resource.USER, Action.CREATE) == Scope.ALL


def test_a_system_principal_without_an_employee_record_still_resolves(everyone):
    """
    Step 7 of the pipeline: a role flagged `requires_employee=False` keeps only
    ALL. Admin and CEO have no Employee row, and must not therefore collapse to
    no access at all.
    """
    for code in ("admin", "ceo"):
        user = everyone[code]
        assert getattr(user, "employee", None) is None
        assert can(user, Resource.EMPLOYEE, Action.VIEW) == Scope.ALL


# ======================================================= audit isolation


def test_audit_isolation_matches_employee_isolation(everyone, staff):
    """
    A Department Head sees audit about their own people only.

    Audit is the resource where a scoping mistake is least likely to be noticed
    and most damaging — it describes everything everyone did.
    """
    from apps.audit.models import AuditLog

    for employee in staff.values():
        employee.save()  # generate an audited event per person

    medical_rows = scope_queryset(
        AuditLog.objects.all(),
        everyone["medical_director"],
        resource=Resource.AUDIT_LOG,
        action=Action.VIEW,
    )
    operations_department = staff["cre"].department_id

    assert not medical_rows.filter(
        subject_employee__department_id=operations_department
    ).exists(), "Medical Director sees audit about Operations staff."
    assert medical_rows.filter(subject_employee=staff["therapist"]).exists(), (
        "…and cannot see audit about their own staff."
    )


def test_managers_and_below_hold_no_audit_access_at_all(everyone):
    for role in ("senior_doctor", "operations_manager", "therapist", "cre", "employee"):
        assert not can(everyone[role], Resource.AUDIT_LOG, Action.VIEW), (
            f"{role} can read the audit log."
        )
