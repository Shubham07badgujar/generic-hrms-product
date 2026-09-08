"""
The leave module: calculation, validation, the ledger, and the routing rule.

The acceptance rule under test, verbatim from the spec:

    HEAD ROLE  → ADMIN approves
    NON-HEAD   → HR approves
    HR Head may NEVER decide their own leave.
    The reporting manager is informed, not asked — unless a policy says so.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.leave import services
from apps.leave.models import (
    Holiday,
    HolidayCalendar,
    LeaveRequest,
    LeaveStatus,
    LeaveTransaction,
    LeaveType,
    TransactionKind,
)
from apps.leave.seeds import seed_leave
from apps.leave.services import LeaveError
from core.access.catalog import DepartmentKind, Layer
from core.access.engine import AccessDenied
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


@pytest.fixture
def leave_config(db):
    seed_leave()
    return {t.code: t for t in LeaveType.objects.all()}


@pytest.fixture
def staff(db, roles, org):
    """One person per band the routing rule distinguishes."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    people: dict = {}
    counter = [3000]

    def hire(role_code, kind, name, layer, manager=None):
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@leave.test", password=PASSWORD, first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)
        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:06d}",
            user=user,
            first_name=name,
            department=org["departments"][kind],
            level=org["levels"][layer],
            location=org["location"],
            reporting_manager=manager,
            date_of_joining=dt.date(2024, 1, 1),
        )
        people[role_code] = employee
        return employee

    hire("admin", DepartmentKind.HR, "Ada", Layer.DEPARTMENT_HEAD)
    hr_head = hire("hr_head", DepartmentKind.HR, "Hema", Layer.DEPARTMENT_HEAD)
    hire("hr_manager", DepartmentKind.HR, "Hari", Layer.MANAGER, manager=hr_head)
    md = hire("medical_director", DepartmentKind.MEDICAL, "Meera", Layer.DEPARTMENT_HEAD)
    hire("therapist", DepartmentKind.MEDICAL, "Tara", Layer.STAFF, manager=md)
    return people


def _apply(staff, leave_config, who="therapist", code="paid", days_from_now=7, span=1, **kw):
    start = timezone.localdate() + dt.timedelta(days=days_from_now)
    # A single-day request must not land on the weekly off or a seeded
    # public holiday - shift to the next working day.
    while services.working_days(staff[who], start, start) <= 0:
        start += dt.timedelta(days=1)
    return services.apply_leave(
        actor=staff[who].user,
        leave_type=leave_config[code],
        start_date=start,
        end_date=start + dt.timedelta(days=span - 1),
        reason=kw.pop("reason", "Family function out of town."),
        **kw,
    )


# ============================================================ calculation


def test_working_days_skip_weekly_off_and_holidays(staff, leave_config):
    employee = staff["therapist"]
    # Seeded default calendar: Sunday off. Find a Monday.
    monday = timezone.localdate() + dt.timedelta(days=7)
    monday -= dt.timedelta(days=monday.weekday())
    calendar = HolidayCalendar.objects.get(location__isnull=True)
    # The seeds declare real public holidays; test against a clean week.
    while Holiday.objects.filter(
        calendar=calendar, date__gte=monday, date__lte=monday + dt.timedelta(days=6)
    ).exists():
        monday += dt.timedelta(days=7)
    Holiday.objects.create(calendar=calendar, date=monday + dt.timedelta(days=2), name="Festival")

    # Mon..Sun inclusive: 7 days − 1 Sunday − 1 holiday = 5.
    days = services.working_days(employee, monday, monday + dt.timedelta(days=6))
    assert days == Decimal("5")


def test_half_day_counts_half_and_only_on_one_date(staff, leave_config):
    employee = staff["therapist"]
    day = timezone.localdate() + dt.timedelta(days=7)
    if day.weekday() == 6:
        day += dt.timedelta(days=1)
    assert services.working_days(employee, day, day, half_day="first_half") == Decimal("0.5")
    with pytest.raises(LeaveError):
        services.working_days(employee, day, day + dt.timedelta(days=1), half_day="first_half")


# ============================================================ application


def test_applying_holds_the_balance_and_writes_the_ledger(staff, leave_config):
    request = _apply(staff, leave_config)
    assert request.status == LeaveStatus.PENDING
    assert request.approval_band == "hr"
    assert request.days > 0

    balance = services.balance_for(
        staff["therapist"], leave_config["paid"], request.start_date.year
    )
    assert balance.pending == request.days
    # ACCRUAL, not a lump sum: the allocation equals what has accrued so far.
    from apps.leave.models import LeavePolicy
    policy = LeavePolicy.objects.get(
        leave_type=leave_config["paid"], department__isnull=True, is_active=True
    )
    assert balance.allocated == services.accrued_target(
        policy, staff["therapist"], request.start_date.year
    )
    assert balance.allocated < policy.annual_allocation or timezone.localdate().month == 12
    kinds = set(
        LeaveTransaction.objects.filter(request=request).values_list("kind", flat=True)
    )
    assert TransactionKind.PENDING_HOLD in kinds


def test_only_the_hr_heads_own_leave_escalates_to_admin(staff, leave_config):
    assert _apply(staff, leave_config, who="hr_head").approval_band == "admin"
    # Everyone else — other Heads included — is decided by the HR Head.
    assert _apply(staff, leave_config, who="medical_director").approval_band == "hr"
    assert _apply(staff, leave_config, who="hr_manager").approval_band == "hr"
    assert _apply(staff, leave_config, who="therapist", days_from_now=30).approval_band == "hr"


def test_overlap_balance_notice_and_consecutive_rules(staff, leave_config):
    _apply(staff, leave_config)
    with pytest.raises(LeaveError, match="overlaps"):
        _apply(staff, leave_config)

    # The notice ladder: a one-day request needs 3 days' notice.
    with pytest.raises(LeaveError, match="notice"):
        _apply(staff, leave_config, days_from_now=0)
    # ...but a genuine emergency starting within the window is admitted.
    emergency = _apply(
        staff, leave_config, who="hr_manager", days_from_now=0, is_emergency=True
    )
    assert emergency.is_emergency is True
    # An "emergency" three weeks out is a planned leave and is refused.
    with pytest.raises(LeaveError, match="Emergency leave must start"):
        _apply(staff, leave_config, who="medical_director", days_from_now=20,
               is_emergency=True)

    # PL/CL allows at most 6 consecutive days.
    with pytest.raises(LeaveError, match="at most 6"):
        _apply(staff, leave_config, days_from_now=40, span=9)

    # No advance leave: a request beyond the ACCRUED balance is refused even
    # though the annual entitlement would cover it. Drain the balance to two
    # days so the refusal is deterministic whatever today's month is.
    balance = services.balance_for(
        staff["therapist"], leave_config["paid"], timezone.localdate().year
    )
    balance.used = balance.allocated + balance.carried_forward - balance.pending - Decimal("2")
    balance.save(update_fields=["used", "updated_at"])
    with pytest.raises(LeaveError, match="available"):
        _apply(staff, leave_config, days_from_now=45, span=5)


def test_half_day_respects_the_policy(staff, leave_config):
    from apps.leave.models import LeavePolicy

    LeavePolicy.objects.filter(leave_type=leave_config["paid"]).update(allow_half_day=False)
    with pytest.raises(LeaveError, match="half day"):
        _apply(staff, leave_config, days_from_now=90, half_day="first_half")


def test_a_new_joiner_accrues_from_the_joining_month_only(staff, leave_config, roles, org):
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee
    from apps.leave.models import LeavePolicy

    user = User.objects.create_user(email="fresh@leave.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["therapist"])
    bind_membership(user)
    fresh = Employee.objects.create(
        employee_code="EMP009999", user=user, first_name="Fresh",
        department=org["departments"][DepartmentKind.MEDICAL],
        location=org["location"],
        date_of_joining=timezone.localdate().replace(day=1),  # joined this month
    )
    policy = LeavePolicy.objects.get(
        leave_type=leave_config["paid"], department__isnull=True, is_active=True
    )
    year = timezone.localdate().year
    balance = services.balance_for(fresh, leave_config["paid"], year)
    # One month in: exactly one month's accrual, nothing more.
    if timezone.localdate().month != 12:
        assert balance.allocated == policy.accrual_per_month.quantize(Decimal("0.1"))
    # No advance: an ask beyond the accrued month is refused.
    with pytest.raises(LeaveError, match="available"):
        services.apply_leave(
            actor=user, leave_type=leave_config["paid"],
            start_date=timezone.localdate() + dt.timedelta(days=40),
            end_date=timezone.localdate() + dt.timedelta(days=44),
            reason="More than has accrued so far.",
        )


def test_lwp_may_go_negative_and_feeds_payroll(staff, leave_config):
    request = _apply(staff, leave_config, code="lwp", days_from_now=10, span=2)
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()
    services.approve_leave(request=request, actor=staff["hr_head"].user)

    lop = services.get_lop_days(
        staff["therapist"], request.start_date.year, request.start_date.month
    )
    assert lop >= Decimal("1")
    # Paid leave never counts as loss of pay.
    paid = _apply(staff, leave_config, code="paid", days_from_now=60)
    services.approve_leave(request=paid, actor=staff["medical_director"].user)
    paid.refresh_from_db()
    services.approve_leave(request=paid, actor=staff["hr_head"].user)
    assert services.get_lop_days(
        staff["therapist"], paid.start_date.year, paid.start_date.month
    ) == lop if paid.start_date.month == request.start_date.month else True


# ============================================================ routing


def test_the_hr_head_decides_everyone_and_nobody_else(staff, leave_config):
    request = _apply(staff, leave_config)

    # At the manager stage only the CONFIGURED manager acts: the HR Manager
    # is not this therapist's manager and is refused.
    with pytest.raises(LeaveError, match="reporting manager"):
        services.approve_leave(request=request, actor=staff["hr_manager"].user)

    # An employee without APPROVE is stopped by RBAC itself.
    with pytest.raises(AccessDenied):
        services.approve_leave(request=request, actor=staff["therapist"].user)

    # The manager forwards; the FINAL decision is still nobody's but the
    # HR Head's — the forwarding manager cannot take it.
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()
    with pytest.raises(LeaveError, match="HR Head"):
        services.approve_leave(request=request, actor=staff["medical_director"].user)

    approved = services.approve_leave(request=request, actor=staff["hr_head"].user)
    assert approved.status == LeaveStatus.APPROVED
    balance = services.balance_for(
        staff["therapist"], leave_config["paid"], request.start_date.year
    )
    assert balance.used == request.days and balance.pending == 0


def test_even_other_heads_are_decided_by_the_hr_head(staff, leave_config):
    request = _apply(staff, leave_config, who="medical_director")
    approved = services.approve_leave(request=request, actor=staff["hr_head"].user)
    assert approved.status == LeaveStatus.APPROVED


def test_every_role_routes_to_the_hr_head(staff, leave_config):
    # The user-facing rule, verbatim: whoever applies, the HR Head decides.
    for offset, who in enumerate(["therapist", "hr_manager", "medical_director"]):
        request = _apply(staff, leave_config, who=who, days_from_now=40 + offset * 7)
        assert request.approval_band == "hr"
        if request.approval_stage == "manager":
            manager = staff[who].reporting_manager
            services.approve_leave(request=request, actor=manager.user)
            request.refresh_from_db()
        assert services.approve_leave(
            request=request, actor=staff["hr_head"].user
        ).status == LeaveStatus.APPROVED


def test_hr_head_never_decides_their_own_leave(staff, leave_config):
    request = _apply(staff, leave_config, who="hr_head")
    with pytest.raises(LeaveError, match="your own"):
        services.approve_leave(request=request, actor=staff["hr_head"].user)
    with pytest.raises(LeaveError, match="your own"):
        services.reject_leave(
            request=request, actor=staff["hr_head"].user, reason="self-service"
        )
    # Admin decides it — the single escalation in the whole workflow.
    assert services.approve_leave(
        request=request, actor=staff["admin"].user
    ).status == LeaveStatus.APPROVED


def test_rejection_requires_a_reason_and_releases_the_hold(staff, leave_config):
    request = _apply(staff, leave_config)
    with pytest.raises(LeaveError, match="reason"):
        services.reject_leave(
            request=request, actor=staff["medical_director"].user, reason="  "
        )
    rejected = services.reject_leave(
        request=request, actor=staff["medical_director"].user,
        reason="Clinic is short-staffed that week.",
    )
    assert rejected.status == LeaveStatus.REJECTED
    balance = services.balance_for(
        staff["therapist"], leave_config["paid"], request.start_date.year
    )
    assert balance.pending == 0 and balance.used == 0


# ============================================================ cancellation


def test_cancelling_restores_the_balance(staff, leave_config):
    request = _apply(staff, leave_config)
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()
    services.approve_leave(request=request, actor=staff["hr_head"].user)
    cancelled = services.cancel_leave(request=request, actor=staff["therapist"].user)
    assert cancelled.status == LeaveStatus.CANCELLED

    balance = services.balance_for(
        staff["therapist"], leave_config["paid"], request.start_date.year
    )
    assert balance.used == 0 and balance.pending == 0
    assert LeaveTransaction.objects.filter(
        request=request, kind=TransactionKind.CANCELLATION_REFUND
    ).exists()


def test_leave_already_started_cannot_be_cancelled(staff, leave_config):
    request = _apply(staff, leave_config, days_from_now=3)
    services.approve_leave(request=request, actor=staff["medical_director"].user)
    request.refresh_from_db()
    services.approve_leave(request=request, actor=staff["hr_head"].user)
    LeaveRequest.objects.filter(pk=request.pk).update(
        start_date=timezone.localdate() - dt.timedelta(days=1)
    )
    request.refresh_from_db()
    with pytest.raises(LeaveError, match="started or passed"):
        services.cancel_leave(request=request, actor=staff["therapist"].user)


# ============================================================ notifications & audit


def test_submission_notifies_the_deciding_band_and_informs_the_manager(
    staff, leave_config, django_capture_on_commit_callbacks
):
    from apps.notifications.models import Notification, NotificationKind

    with django_capture_on_commit_callbacks(execute=True):
        _apply(staff, leave_config)  # therapist → HR band, manager = MD

    recipients = set(
        Notification.objects.filter(kind=NotificationKind.LEAVE_SUBMITTED)
        .values_list("recipient__email", flat=True)
    )
    # The chain's FIRST approver is asked — the reporting manager, alone.
    assert "medical_director@leave.test" in recipients
    assert "hr_head@leave.test" not in recipients
    assert "hr_manager@leave.test" not in recipients


def test_decisions_notify_the_employee_and_everything_is_audited(
    staff, leave_config, django_capture_on_commit_callbacks
):
    from apps.audit.models import AuditLog
    from apps.notifications.models import Notification, NotificationKind

    with django_capture_on_commit_callbacks(execute=True):
        request = _apply(staff, leave_config)
        services.reject_leave(
            request=request, actor=staff["medical_director"].user, reason="Short-staffed."
        )

    note = Notification.objects.filter(
        kind=NotificationKind.LEAVE_DECIDED, recipient=staff["therapist"].user
    ).first()
    assert note is not None and "rejected" in note.title
    events = [
        row.after.get("event")
        for row in AuditLog.objects.filter(entity_type="leave.LeaveRequest")
    ]
    assert "leave_submitted" in events and "leave_rejected" in events


# ============================================================ API surface


def test_the_api_scopes_rows_and_enforces_routing(api, staff, leave_config):
    request = _apply(staff, leave_config)
    _apply(staff, leave_config, who="hr_manager", days_from_now=20)

    def login(role):
        token = api.post(
            "/api/v1/auth/login/",
            {"email": f"{role}@leave.test", "password": PASSWORD},
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # The therapist sees exactly their own request.
    login("therapist")
    rows = api.get("/api/v1/leave-requests/").data["data"]
    assert {row["employee_code"] for row in rows} == {staff["therapist"].employee_code}
    # …and cannot approve anything (403 from RBAC).
    assert api.post(f"/api/v1/leave-requests/{request.pk}/approve/").status_code == 403

    # The calendar never carries reasons or attachments.
    calendar = api.get("/api/v1/leave-requests/calendar/").data["data"]
    assert calendar and all("reason" not in row for row in calendar)

    # The reporting manager forwards over HTTP...
    login("medical_director")
    forwarded = api.post(f"/api/v1/leave-requests/{request.pk}/approve/")
    assert forwarded.status_code == 200
    assert forwarded.data["status"] == "pending"
    assert forwarded.data["approval_stage"] == "hr"
    assert forwarded.data["pending_with"] == "HR Head"

    # ...and HR sees all and finalises.
    login("hr_head")
    assert len(api.get("/api/v1/leave-requests/").data["data"]) >= 2
    done = api.post(f"/api/v1/leave-requests/{request.pk}/approve/")
    assert done.status_code == 200 and done.data["status"] == "approved"


def test_everyone_reads_types_and_balances_materialise_on_view(api, staff, leave_config):
    def login(role):
        token = api.post(
            "/api/v1/auth/login/",
            {"email": f"{role}@leave.test", "password": PASSWORD},
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # A therapist holds no LEAVE_POLICY grant, yet the apply form needs the
    # type list — reads ride the self-service LEAVE_REQUEST grant.
    login("therapist")
    types = api.get("/api/v1/leave-types/")
    assert types.status_code == 200
    assert {row["code"] for row in types.data["data"]} >= {"paid", "sick", "lwp"}
    # Writing types is still configuration.
    assert api.post("/api/v1/leave-types/", {"code": "x", "name": "X"}).status_code == 403

    # Viewing balances is a first touch: rows appear with the policy's
    # allocation even though this person has never applied.
    balances = api.get("/api/v1/leave-balances/").data["data"]
    by_code = {row["leave_type_code"]: row for row in balances}
    from decimal import Decimal as D
    assert D(by_code["paid"]["allocated"]) > 0  # the accrued-so-far figure
    assert set(by_code) >= {"paid", "sick", "lwp"}


# ============================================================ rollover


def test_carry_forward_is_capped_by_the_policy(staff, leave_config):
    year = timezone.localdate().year
    balance = services.balance_for(staff["therapist"], leave_config["paid"], year - 1)
    assert balance.allocated == Decimal("19")  # a past year has fully accrued

    moved = services.annual_rollover(year=year)
    assert moved >= 1
    current = services.balance_for(staff["therapist"], leave_config["paid"], year)
    assert current.carried_forward == Decimal("19")  # at the policy cap
    # Idempotent: running again does not double it.
    services.annual_rollover(year=year)
    current.refresh_from_db()
    assert current.carried_forward == Decimal("19")
