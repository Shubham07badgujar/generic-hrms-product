"""
Employee-first atomic creation.

Every test here defends one of two properties:
  1. Invalid input is refused BEFORE anything is written.
  2. If any step fails, NOTHING remains — no orphan login, no orphan employee.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User, UserRole
from apps.employees.models import Employee
from apps.employees.services.creation import create_employee
from core.access import AccessDenied
from core.access.catalog import DepartmentKind, Layer

pytestmark = pytest.mark.django_db


def _counts():
    return User.objects.count(), Employee.objects.count(), UserRole.objects.count()


# ============================================================ success


def test_creates_person_login_and_role_together(hr_head, valid_payload):
    result = create_employee(actor=hr_head, **valid_payload)

    assert result.employee.pk and result.user.pk
    assert result.employee.user_id == result.user.pk
    assert result.user.employee.pk == result.employee.pk
    assert result.role.code == "therapist"
    assert UserRole.objects.filter(user=result.user, role__code="therapist").exists()


def test_allocates_a_sequential_employee_code(hr_head, valid_payload):
    first = create_employee(actor=hr_head, **valid_payload)

    second = create_employee(
        actor=hr_head, **{**valid_payload, "email": "second@example.test"}
    )

    assert first.employee.employee_code != second.employee.employee_code
    assert first.employee.employee_code.startswith("EMP")


def test_created_employee_resolves_real_permissions(hr_head, valid_payload):
    """
    The point of employee-first creation: the new login is immediately usable.

    A therapist created this way has an Employee, so `requires_employee`
    resolves and self-service permissions appear. Creating the login alone
    would have produced an account that signs in to nothing.
    """
    from core.access import Resource, can

    result = create_employee(actor=hr_head, **valid_payload)

    assert can(result.user, Resource.EMPLOYEE, "view"), (
        "A properly created employee must resolve permissions immediately."
    )


def test_a_temporary_password_is_generated_when_none_is_supplied(hr_head, valid_payload):
    """
    Deliberately the opposite of the previous default. "No usable password until
    set out of band" was safer in theory and meant, in practice, that new
    employees could not log in — no out-of-band channel existed. Now a random
    temporary password is generated, mailed after commit, and flagged so the
    account can do nothing but replace it.
    """
    result = create_employee(actor=hr_head, **valid_payload)

    assert result.user.has_usable_password()
    assert result.temporary_password
    assert result.user.check_password(result.temporary_password)
    assert result.user.must_change_password


def test_temporary_password_forces_a_change(hr_head, valid_payload):
    result = create_employee(
        actor=hr_head, temporary_password="Temp-Password-12345", **valid_payload
    )

    assert result.user.has_usable_password()
    assert result.user.must_change_password


def test_department_head_may_have_no_reporting_manager(hr_head, org):
    """Layer-2 heads answer to the CEO, who has no Employee record to point at."""
    result = create_employee(
        actor=hr_head,
        first_name="Farah",
        last_name="Sheikh",
        email="finance.head@example.test",
        role_code="finance_head",
        department_id=org["departments"][DepartmentKind.FINANCE].pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.DEPARTMENT_HEAD].pk,
        reporting_manager_id=None,
        date_of_joining=dt.date(2026, 1, 1),
    )

    assert result.employee.reporting_manager_id is None


# ============================================================ duplicates


def test_duplicate_email_is_refused(hr_head, valid_payload):
    create_employee(actor=hr_head, **valid_payload)
    before = _counts()

    with pytest.raises(ValidationError, match="already exists"):
        create_employee(actor=hr_head, **valid_payload)

    assert _counts() == before, "A rejected duplicate must write nothing."


def test_email_is_normalised_before_the_duplicate_check(hr_head, valid_payload):
    """
    Case must not create a second account for the same person.

    Postgres comparison is case-sensitive, so without normalisation
    Priya@… and priya@… would be two logins for one human.
    """
    create_employee(actor=hr_head, **valid_payload)

    with pytest.raises(ValidationError, match="already exists"):
        create_employee(
            actor=hr_head, **{**valid_payload, "email": valid_payload["email"].upper()}
        )


# ================================================ role / department rules


def test_role_must_match_department_function(hr_head, org):
    """An HR Head in the Medical department is a contradiction."""
    before = _counts()

    with pytest.raises(ValidationError, match="function"):
        create_employee(
            actor=hr_head,
            first_name="Wrong",
            email="wrong@example.test",
            role_code="hr_head",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            date_of_joining=dt.date(2026, 1, 1),
        )

    assert _counts() == before


def test_department_agnostic_role_is_accepted_anywhere(hr_head, org, medical_director):
    """`employee` has no functional home and must be allowed in any department."""
    result = create_employee(
        actor=hr_head,
        first_name="Generic",
        email="generic@example.test",
        role_code="employee",
        department_id=org["departments"][DepartmentKind.FINANCE].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=medical_director.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )

    assert result.employee.pk


def test_level_must_match_the_role_layer(hr_head, org, medical_director):
    with pytest.raises(ValidationError, match="band"):
        create_employee(
            actor=hr_head,
            first_name="Mismatched",
            email="mismatch@example.test",
            role_code="therapist",  # Layer 5
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            level_id=org["levels"][Layer.DEPARTMENT_HEAD].pk,  # Layer 2
            reporting_manager_id=medical_director.pk,
            date_of_joining=dt.date(2026, 1, 1),
        )


def test_designation_from_another_department_is_refused(hr_head, org, medical_director):
    with pytest.raises(ValidationError, match="another department"):
        create_employee(
            actor=hr_head,
            first_name="Cross",
            email="cross@example.test",
            role_code="employee",
            department_id=org["departments"][DepartmentKind.FINANCE].pk,
            designation_id=org["designation"].pk,  # a Medical designation
            reporting_manager_id=medical_director.pk,
            date_of_joining=dt.date(2026, 1, 1),
        )


# ============================================== system-level role guards


@pytest.mark.parametrize("system_role", ["ceo", "admin"])
def test_system_level_roles_cannot_be_created_as_employees(hr_head, org, system_role):
    """
    CEO and Admin are authority OVER the organization, not positions within it.

    Creating an Employee for one would give them a department and a manager
    whose scope their access ignores — an org chart that lies.
    """
    before = _counts()

    with pytest.raises(ValidationError, match="system-level"):
        create_employee(
            actor=hr_head,
            first_name="System",
            email=f"{system_role}@example.test",
            role_code=system_role,
            department_id=org["departments"][DepartmentKind.HR].pk,
            designation_id=org["any_designation"].pk,
            date_of_joining=dt.date(2026, 1, 1),
        )

    assert _counts() == before


# ================================================ reporting relationships


def test_a_peer_may_be_the_reporting_manager(hr_head, org, valid_payload):
    """
    Same-layer reporting is allowed: real structures are not a strict ladder,
    and seniority is not what grants permission here — the role is.
    """
    peer = create_employee(actor=hr_head, **valid_payload)

    result = create_employee(
        actor=hr_head,
        first_name="Second",
        email="second.therapist@example.test",
        personal_email="second.therapist@personal.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=peer.employee.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )

    assert result.employee.reporting_manager_id == peer.employee.pk


def test_a_manager_may_not_be_more_junior(hr_head, org, valid_payload):
    """The one direction that stays closed — a line running downward."""
    junior = create_employee(actor=hr_head, **valid_payload)  # Layer 5

    with pytest.raises(ValidationError, match="more junior"):
        create_employee(
            actor=hr_head,
            first_name="Upside",
            email="upside.head@example.test",
            personal_email="upside.head@personal.test",
            role_code="medical_director",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            reporting_manager_id=junior.employee.pk,
            date_of_joining=dt.date(2026, 1, 1),
        )


def test_non_head_requires_a_reporting_manager(hr_head, org):
    with pytest.raises(ValidationError, match="must have a reporting manager"):
        create_employee(
            actor=hr_head,
            first_name="Orphan",
            email="orphan@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            reporting_manager_id=None,
            date_of_joining=dt.date(2026, 1, 1),
        )


def test_cross_department_reporting_is_permitted_upward(hr_head, org, roles):
    """
    A therapist MAY report to a more senior manager in another department.

    The clinic deliberately runs cross-functional lines (its hiring workflows
    assign clinical seniors to operations rounds); the only requirement is
    strict seniority, which the peer test above pins.
    """
    from apps.accounts.models import User as U
    from apps.accounts.models import UserRole as UR
    from apps.employees.models import Employee as E

    manager_user = U.objects.create_user(email="opsmgr@example.test", password="x")
    UR.objects.create(user=manager_user, role=roles["operations_manager"])
    manager = E.objects.create(
        employee_code="EMP09000",
        user=manager_user,
        first_name="Ops",
        department=org["departments"][DepartmentKind.OPERATIONS],
        date_of_joining=dt.date(2020, 1, 1),
    )

    result = create_employee(
        actor=hr_head,
        first_name="Crossline",
        email="crossline@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=manager.pk,
        date_of_joining=dt.date(2026, 1, 1),
    )
    assert result.employee.reporting_manager_id == manager.pk


def test_manager_without_a_role_is_refused(hr_head, org):
    """Approvals routed to a role-less manager would go nowhere."""
    from apps.employees.models import Employee as E

    roleless = E.objects.create(
        employee_code="EMP09001",
        first_name="Roleless",
        department=org["departments"][DepartmentKind.MEDICAL],
        date_of_joining=dt.date(2020, 1, 1),
    )

    with pytest.raises(ValidationError, match="no active role"):
        create_employee(
            actor=hr_head,
            first_name="Reports",
            email="reports@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            reporting_manager_id=roleless.pk,
            date_of_joining=dt.date(2026, 1, 1),
        )


def test_unknown_reporting_manager_is_refused(hr_head, valid_payload):
    import uuid

    with pytest.raises(ValidationError, match="reporting_manager"):
        create_employee(
            actor=hr_head, **{**valid_payload, "reporting_manager_id": uuid.uuid4()}
        )


# ================================================== creator authorisation


def test_unauthorised_creator_is_refused(make_user, valid_payload):
    """A therapist holds no USER/CREATE and must not be able to create anyone."""
    therapist = make_user("therapist")

    with pytest.raises(AccessDenied):
        create_employee(actor=therapist, **valid_payload)


def test_creator_cannot_mint_someone_more_senior(db, roles, org, medical_director):
    """
    An HR Manager (Layer 3) holds USER/CREATE but must not create a Layer-2 head.

    Otherwise a delegated creator could manufacture authority above their own.
    """
    from apps.accounts.models import User as U
    from apps.accounts.models import UserRole as UR
    from apps.employees.models import Employee as E

    user = U.objects.create_user(email="hrmgr@example.test", password="x")
    UR.objects.create(user=user, role=roles["hr_manager"])
    E.objects.create(
        employee_code="EMP09002",
        user=user,
        first_name="Manager",
        department=org["departments"][DepartmentKind.HR],
        date_of_joining=dt.date(2020, 1, 1),
    )

    with pytest.raises(ValidationError, match="your own authority"):
        create_employee(
            actor=user,
            first_name="TooSenior",
            email="toosenior@example.test",
            role_code="medical_director",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            reporting_manager_id=None,
            date_of_joining=dt.date(2026, 1, 1),
        )


def test_hr_head_may_create_a_peer_level_department_head(hr_head, org):
    """Equal-layer creation IS allowed — the approved hierarchy requires it."""
    result = create_employee(
        actor=hr_head,
        first_name="Peer",
        email="peer.head@example.test",
        role_code="operational_head",
        department_id=org["departments"][DepartmentKind.OPERATIONS].pk,
        designation_id=org["any_designation"].pk,
        reporting_manager_id=None,
        date_of_joining=dt.date(2026, 1, 1),
    )

    assert result.role.code == "operational_head"


# ==================================================== transaction safety


def test_nothing_persists_when_a_late_step_fails(hr_head, valid_payload, monkeypatch):
    """
    THE CORE GUARANTEE.

    Force a failure AFTER the User and Employee have been written, and assert
    the whole transaction unwinds. Without this, a crash between the login and
    the role grant would leave an account that can sign in and see nothing —
    the exact half-created state the design forbids.
    """
    from apps.employees.services import creation

    before = _counts()

    def explode(*args, **kwargs):
        raise RuntimeError("simulated failure after the employee was written")

    monkeypatch.setattr(creation, "_audit_creation", explode)

    with pytest.raises(RuntimeError, match="simulated failure"):
        create_employee(actor=hr_head, **valid_payload)

    assert _counts() == before, (
        "A failure at any point must leave no user, employee or role grant."
    )
    assert not User.objects.filter(email=valid_payload["email"]).exists()


def test_the_rollback_test_is_meaningful(hr_head, valid_payload, monkeypatch):
    """
    Prove the rollback test above is not passing trivially.

    If the failure point came BEFORE any write, `test_nothing_persists...`
    would pass against a completely unprotected implementation. This captures
    the row counts at the moment of failure and asserts the user, employee and
    role grant all existed — so the subsequent rollback is doing real work.
    """
    from apps.employees.services import creation

    observed = {}

    def capture_then_explode(*args, **kwargs):
        observed["counts"] = _counts()
        raise RuntimeError("simulated")

    monkeypatch.setattr(creation, "_audit_creation", capture_then_explode)

    with pytest.raises(RuntimeError):
        create_employee(actor=hr_head, **valid_payload)

    users, employees, grants = observed["counts"]
    assert users >= 2 and employees >= 2 and grants >= 2, (
        f"Expected the new user/employee/grant to exist at failure time, saw "
        f"{observed['counts']}. If they did not, the rollback test proves nothing."
    )


def test_no_orphan_login_when_employee_validation_fails(hr_head, org, medical_director):
    """
    The User is written before the Employee, so a model-level Employee failure
    is precisely where an orphan account would appear if the transaction were
    not doing its job.
    """
    before = _counts()

    with pytest.raises(ValidationError):
        create_employee(
            actor=hr_head,
            first_name="",  # blank first name fails Employee.full_clean
            email="orphan.check@example.test",
            role_code="therapist",
            department_id=org["departments"][DepartmentKind.MEDICAL].pk,
            designation_id=org["any_designation"].pk,
            reporting_manager_id=medical_director.pk,
            date_of_joining=dt.date(2026, 1, 1),
        )

    assert _counts() == before
    assert not User.objects.filter(email="orphan.check@example.test").exists()


def test_every_employee_with_a_login_has_a_role(hr_head, valid_payload):
    """Consistency invariant across everything this service creates."""
    create_employee(actor=hr_head, **valid_payload)

    for employee in Employee.objects.filter(user__isnull=False):
        assert employee.user.user_roles.filter(is_active=True).exists(), (
            f"{employee} has a login with no role — it would resolve to no access."
        )


# ================================================== audit attribution


def test_creation_is_attributed_to_the_acting_user(hr_head, valid_payload):
    from apps.audit.models import AuditAction, AuditLog

    result = create_employee(actor=hr_head, **valid_payload)

    # Filtered to the CREATE event rather than taking the newest row: creation
    # now also starts probation and issues a checklist, each of which writes its
    # own entry, so "the latest row" is no longer the one under test.
    entry = AuditLog.objects.filter(
        entity_type="employees.Employee",
        entity_id=str(result.employee.pk),
        action=AuditAction.CREATE,
    ).first()

    assert entry is not None
    assert entry.actor_id == hr_head.pk
    assert entry.actor_email == hr_head.email
    assert entry.after["role"] == "therapist"


def test_created_rows_carry_created_by(hr_head, valid_payload):
    """
    `BaseModel.save()` stamps created_by from the request context.

    Called outside a request here, so it exercises the ContextVar directly —
    the same mechanism JWT authentication binds into.
    """
    from core.middleware import acting_as

    with acting_as(hr_head):
        result = create_employee(actor=hr_head, **valid_payload)

    result.employee.refresh_from_db()
    assert result.employee.created_by_id == hr_head.pk


# ============================================================ agreed CTC


def test_annual_ctc_is_recorded_at_hire(hr_head, valid_payload):
    """The Add Employee form's Salary field lands on the record, informationally."""
    from decimal import Decimal

    result = create_employee(actor=hr_head, **valid_payload, annual_ctc=Decimal("480000"))
    assert result.employee.annual_ctc == Decimal("480000")


def test_annual_ctc_is_need_to_know(hr_head, valid_payload):
    """
    The detail serializer releases the agreed CTC only to SALARY viewers and
    the person themselves — anyone else sees null, not a masked hint.
    """
    from decimal import Decimal

    from apps.employees.api.serializers import EmployeeDetailSerializer

    result = create_employee(actor=hr_head, **valid_payload, annual_ctc=Decimal("480000"))
    employee = result.employee

    class _Req:
        def __init__(self, user):
            self.user = user

    # HR Head holds SALARY/VIEW at ALL — sees it.
    shown = EmployeeDetailSerializer(employee, context={"request": _Req(hr_head)}).data
    assert shown["annual_ctc"] == "480000.00"

    # The person themselves — sees their own.
    own = EmployeeDetailSerializer(employee, context={"request": _Req(employee.user)}).data
    assert own["annual_ctc"] == "480000.00"

    # A plain employee looking at a colleague — sees nothing.
    other = create_employee(
        actor=hr_head,
        **{**valid_payload, "email": "peer@example.test", "first_name": "Peer"},
    )
    peers = EmployeeDetailSerializer(
        employee, context={"request": _Req(other.user)}
    ).data
    assert peers["annual_ctc"] is None
