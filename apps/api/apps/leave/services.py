"""
Leave services: apply, decide, cancel, and the numbers behind them.

EVERYTHING IS SERVER-SIDE. The client sends dates and a reason; the working
days, the balance movement, the approver band and the approver's authority are
all computed and enforced here. No employee id, approver id, day count or
balance from a request body is ever trusted.

ROUTING — the office's acceptance rule, verbatim:

              LEAVE REQUEST
                    │
         REPORTING MANAGER  (skipped when none is configured,
                    │        or the manager IS the final authority)
              approve = forward · reject = END
                    │
                 HR HEAD     (Admin, for the HR Head's own request)
                    │
             final approve / reject

The manager comes from the employee's CONFIGURED reporting_manager — never a
hard-coded person. The manager's approval grants nothing: no balance moves
until the final authority approves. Nobody — HR Head included — may ever
decide their own request.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import Action, Resource, allows, require
from core.access.catalog import RoleCode
from core.validators import validate_upload

from .models import (
    ApprovalAuthority,
    HalfDay,
    Holiday,
    HolidayCalendar,
    HolidayWork,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    LeaveSettings,
    LeaveStatus,
    LeaveTransaction,
    LeaveType,
    ShortLeave,
    ShortLeaveConversion,
    TransactionKind,
)

ZERO = Decimal("0")
HALF = Decimal("0.5")
#: What a leave attachment may be: the same shapes a medical certificate or
#: fitness note actually arrives in. `validate_upload` takes extensions WITH
#: the leading dot — without it every upload was refused, which made the
#: certificate-required sick leave impossible to file at all.
ATTACHMENT_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"})
ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
#: Weekly off when no calendar is configured at all: Sunday.
DEFAULT_WEEKLY_OFF = [6]


class LeaveError(ValidationError):
    """A refused leave operation."""


# ------------------------------------------------------------------ calendar


def calendar_for(employee) -> HolidayCalendar | None:
    """The employee's location calendar, falling back to the org default."""
    if employee.location_id:
        row = HolidayCalendar.objects.filter(
            location_id=employee.location_id, is_active=True
        ).first()
        if row:
            return row
    return HolidayCalendar.objects.filter(location__isnull=True, is_active=True).first()


def working_days(employee, start: dt.date, end: dt.date, *, half_day: str = "") -> Decimal:
    """
    Working days between start and end inclusive: calendar days minus the
    employee's weekly offs and holidays. A half day counts 0.5 and only ever
    applies to a single working date.
    """
    calendar = calendar_for(employee)
    weekly_off = set(
        calendar.weekly_off if calendar and calendar.weekly_off else DEFAULT_WEEKLY_OFF
    )
    holidays = set()
    if calendar:
        holidays = set(
            Holiday.objects.filter(
                calendar=calendar, date__range=(start, end), is_active=True
            ).values_list("date", flat=True)
        )

    count = ZERO
    day = start
    while day <= end:
        if day.weekday() not in weekly_off and day not in holidays:
            count += Decimal("1")
        day += dt.timedelta(days=1)

    if half_day:
        if start != end:
            raise LeaveError({"half_day": "A half day applies to a single date only."})
        if count == ZERO:
            return ZERO
        return HALF
    return count


# ------------------------------------------------------------------ policies


def policy_for(employee, leave_type: LeaveType) -> LeavePolicy | None:
    """Most specific active policy: dept+type, dept, employment type, default."""
    rows = LeavePolicy.objects.filter(leave_type=leave_type, is_active=True)
    return (
        rows.filter(department_id=employee.department_id,
                    employment_type=employee.employment_type).first()
        or rows.filter(department_id=employee.department_id, employment_type="").first()
        or rows.filter(department__isnull=True,
                       employment_type=employee.employment_type).first()
        or rows.filter(department__isnull=True, employment_type="").first()
    )


def _entitlement_pending_probation(employee) -> bool:
    """Probation not yet decided — the states where entitlement waits for HR."""
    from apps.employees.models import ProbationStatus

    return employee.probation_status in (
        ProbationStatus.ACTIVE, ProbationStatus.DUE, ProbationStatus.EXTENDED,
    )


def accrued_target(policy: LeavePolicy, employee, year: int, today=None) -> Decimal:
    """
    How much of the annual allocation has ACCRUED by today.

    Accrual credits at the start of each month: `accrual_per_month × months
    elapsed` (joining month onwards for a mid-year joiner), capped at the
    annual allocation — and December always completes the full entitlement,
    so rounding of the monthly rate never shorts the year.

    PROBATION: unless the policy explicitly opts in, nothing accrues while
    probation is undecided, and a confirmed employee accrues from the
    confirmation month — not retroactively from joining. Employees whose
    probation is NOT_APPLICABLE, or long-confirmed staff, are untouched.
    """
    today = today or timezone.localdate()

    entitlement_from = employee.date_of_joining
    if not policy.accrues_during_probation:
        if _entitlement_pending_probation(employee):
            return ZERO
        if employee.confirmation_date:
            entitlement_from = max(
                entitlement_from or employee.confirmation_date, employee.confirmation_date
            )

    if not policy.accrual_per_month:
        return policy.annual_allocation

    first_month = 1
    joined = entitlement_from
    if joined and joined.year == year:
        first_month = joined.month
    elif joined and joined.year > year:
        return ZERO

    if today.year > year:
        current_month = 12
    elif today.year < year:
        return ZERO
    else:
        current_month = today.month

    months = max(0, current_month - first_month + 1)
    if current_month == 12:
        return policy.annual_allocation
    target = (policy.accrual_per_month * months).quantize(Decimal("0.1"))
    return min(target, policy.annual_allocation)


def _refresh_accrual(balance: LeaveBalance, policy: LeavePolicy, *, actor=None) -> LeaveBalance:
    """
    Sync the allocated figure to the accrued target, with a ledger row.

    Both directions: up as months accrue, and DOWN when the target shrinks —
    which happens exactly when probation gating applies to an employee whose
    balance accrued under the old rule. Never below what is already used or
    held pending, so no approved leave is stranded. For confirmed employees
    the target computes as before, so nothing moves.
    """
    target = accrued_target(policy, balance.employee, balance.year)
    floor = balance.used + balance.pending
    target = max(target, floor)
    delta = target - balance.allocated
    if delta == ZERO:
        return balance
    balance.allocated = target
    balance.save(update_fields=["allocated", "updated_at"])
    LeaveTransaction.objects.create(
        employee=balance.employee, leave_type=balance.leave_type, year=balance.year,
        kind=TransactionKind.ALLOCATION if delta > ZERO else TransactionKind.ADJUSTMENT,
        days=delta, actor=actor,
        note=(
            f"Accrual through {timezone.localdate():%b %Y} ({policy.name})"
            if delta > ZERO
            else f"Reconciled to accrued target ({policy.name}) — entitlement "
                 f"begins at probation confirmation"
        ),
    )
    return balance


def balance_for(employee, leave_type: LeaveType, year: int, *, actor=None) -> LeaveBalance:
    """
    The balance row, created with the policy's allocation on first touch —
    and a ledger row explaining where the allocation came from. Accruing
    policies credit only what has accrued to date: no advance leave.
    """
    policy = policy_for(employee, leave_type)
    balance = LeaveBalance.objects.filter(
        employee=employee, leave_type=leave_type, year=year, is_active=True
    ).first()
    if balance:
        # Refresh for up-front policies too, not only accruing ones: the
        # probation gate can move an up-front target (annual ↔ zero), and a
        # confirmed employee's target is stable so the refresh is a no-op.
        if policy:
            _refresh_accrual(balance, policy, actor=actor)
        return balance

    allocation = accrued_target(policy, employee, year) if policy else ZERO
    balance = LeaveBalance.objects.create(
        employee=employee, leave_type=leave_type, year=year, allocated=allocation
    )
    if allocation:
        note = (
            f"Accrual through {timezone.localdate():%b %Y} ({policy.name})"
            if policy and policy.accrual_per_month
            else f"Annual allocation for {year}" + (f" ({policy.name})" if policy else "")
        )
        LeaveTransaction.objects.create(
            employee=employee, leave_type=leave_type, year=year,
            kind=TransactionKind.ALLOCATION, days=allocation, actor=actor, note=note,
        )
    return balance


def ensure_balances(employee, *, year: int | None = None) -> list[LeaveBalance]:
    """
    Materialise this employee's balance rows for every active leave type.

    `balance_for` creates rows on first touch, which used to mean "first
    application" — so a person who had never applied saw no balances at all.
    Viewing the leave page is a first touch too.
    """
    year = year or dt.date.today().year
    return [
        balance_for(employee, leave_type, year)
        for leave_type in LeaveType.objects.filter(is_active=True)
    ]


# ------------------------------------------------------------------ routing


def approval_band(employee) -> str:
    """
    'admin' only when the applicant IS the HR Head; 'hr' for everyone else.

    Final authority is the HR Head for the whole organisation. The one
    request the HR Head cannot decide is their own, so that single case
    escalates to Admin.
    """
    if employee.user_id is None:
        return "hr"
    codes = employee.user.user_roles.filter(
        is_active=True, role__is_active=True
    ).values_list("role__code", flat=True)
    if RoleCode.HR_HEAD in codes:
        return "admin"
    return "hr"


def initial_stage(employee) -> str:
    """
    Where a new request starts: with the CONFIGURED reporting manager when
    one exists and can act, else directly with the final authority.

    "Can act" means the manager has a live login and is not the applicant.
    A manager who IS the final authority (HR Head, or Admin for the HR
    Head's own leave) would be approving twice — the manager step collapses.
    """
    manager = employee.reporting_manager
    if manager is None or manager.user_id is None or manager.pk == employee.pk:
        return "hr"
    if not manager.user.is_active:
        return "hr"
    manager_codes = set(
        manager.user.user_roles.filter(is_active=True, role__is_active=True)
        .values_list("role__code", flat=True)
    )
    if RoleCode.HR_HEAD in manager_codes or RoleCode.ADMIN in manager_codes:
        return "hr"
    # A manager whose roles carry no approval right at all would strand the
    # request at a stage they cannot act on — route past them instead.
    if not allows(manager.user, Resource.LEAVE_REQUEST, Action.APPROVE):
        return "hr"
    return "manager"


def _actor_roles(actor) -> set[str]:
    return set(
        actor.user_roles.filter(is_active=True, role__is_active=True)
        .values_list("role__code", flat=True)
    )


def assert_may_decide(actor, request: LeaveRequest) -> None:
    """
    The routing rule, enforced where it cannot be bypassed.

    Holding LEAVE_REQUEST/APPROVE is necessary but NOT sufficient. The chain
    is Employee -> Reporting Manager -> HR Head (Admin for the HR Head's
    own request): at the MANAGER stage only the applicant's configured
    reporting manager acts (Admin as break-glass); at the HR stage only the
    band's final authority does. Nobody decides their own request.
    """
    require(actor, Resource.LEAVE_REQUEST, Action.APPROVE)

    actor_employee = getattr(actor, "employee", None)
    if actor_employee is not None and actor_employee.pk == request.employee_id:
        raise LeaveError({"request": "You cannot decide your own leave request."})

    roles = _actor_roles(actor)

    if request.approval_stage == "manager":
        if (
            actor_employee is not None
            and request.employee.reporting_manager_id == actor_employee.pk
        ):
            return
        if RoleCode.ADMIN in roles:
            return
        raise LeaveError(
            {
                "request": (
                    "This request is with the applicant's reporting manager. "
                    "It reaches the HR Head after the manager forwards it."
                )
            }
        )

    if request.approval_band == "admin":
        if RoleCode.ADMIN not in roles:
            raise LeaveError(
                {"request": "The HR Head's leave is decided by Admin, not by peers."}
            )
        return

    if roles & {RoleCode.ADMIN, RoleCode.HR_HEAD}:
        return
    raise LeaveError({"request": "The final decision on this request is the HR Head's."})


def may_decide(actor, request: LeaveRequest) -> bool:
    """
    `assert_may_decide` as a question, for the UI: is it THIS caller's turn?

    Keeps dead Approve/Reject buttons off screens that can see a request
    (HR oversight sees everything) but cannot act on it at its current stage.
    """
    if request.status != LeaveStatus.PENDING:
        return False
    if not allows(actor, Resource.LEAVE_REQUEST, Action.APPROVE):
        return False
    try:
        assert_may_decide(actor, request)
    except LeaveError:
        return False
    return True


# ------------------------------------------------------------------ apply


@transaction.atomic
def apply_leave(
    *,
    actor,
    leave_type: LeaveType,
    start_date: dt.date,
    end_date: dt.date,
    reason: str,
    half_day: str = "",
    attachment=None,
    is_emergency: bool = False,
) -> LeaveRequest:
    """
    An employee applies for their OWN leave. Every rule the policy states is
    checked here, against server-side numbers only.
    """
    require(actor, Resource.LEAVE_REQUEST, Action.CREATE)
    employee = getattr(actor, "employee", None)
    if employee is None:
        raise LeaveError({"employee": "Only an employee can apply for leave."})

    if end_date < start_date:
        raise LeaveError({"end_date": "Leave cannot end before it starts."})
    if start_date.year != end_date.year:
        raise LeaveError(
            {"end_date": "A single request cannot span two calendar years — apply per year."}
        )
    if not (reason or "").strip():
        raise LeaveError({"reason": "A reason is required."})
    if half_day and half_day not in HalfDay.values:
        raise LeaveError({"half_day": "Choose first half or second half."})

    policy = policy_for(employee, leave_type)
    if policy is None:
        raise LeaveError(
            {"leave_type": f"No leave policy covers {leave_type.name} for you — ask HR."}
        )

    if half_day and not policy.allow_half_day:
        raise LeaveError({"half_day": f"{leave_type.name} cannot be taken as a half day."})

    if policy.min_service_months:
        eligible_from = employee.date_of_joining + dt.timedelta(
            days=policy.min_service_months * 30
        )
        if start_date < eligible_from:
            raise LeaveError(
                {
                    "leave_type": (
                        f"{leave_type.name} needs {policy.min_service_months} months of "
                        f"service; you are eligible from {eligible_from:%d %b %Y}."
                    )
                }
            )

    days = working_days(employee, start_date, end_date, half_day=half_day)
    if days <= ZERO:
        raise LeaveError(
            {"start_date": "Every day in this range is a weekly off or a holiday."}
        )

    # THE NOTICE LADDER — the handbook's rule, as configuration. An emergency
    # waives notice but must genuinely be one: the leave starts within the
    # emergency window. The per-policy `min_notice_days` still applies as a
    # floor where a policy sets one.
    config = LeaveSettings.get_solo()
    today = timezone.localdate()
    notice = (start_date - today).days
    if is_emergency:
        window_days = max(1, -(-config.emergency_window_hours // 24))
        if notice > window_days:
            raise LeaveError(
                {
                    "start_date": (
                        f"Emergency leave must start within {config.emergency_window_hours} "
                        f"hours — this request starts in {notice} days. Apply as planned leave."
                    )
                }
            )
    else:
        if days > config.long_leave_threshold_days:
            required, label = config.long_leave_notice_days, "long leave"
        elif days <= 1:
            required, label = config.single_day_notice_days, "one-day leave"
        else:
            required, label = config.general_notice_days, "planned leave"
        required = max(required, policy.min_notice_days)
        if notice < required:
            raise LeaveError(
                {
                    "start_date": (
                        f"{label.capitalize()} needs {required} days' notice; this start "
                        f"gives {max(notice, 0)}. For a genuine emergency, tick "
                        f"'emergency leave' — HR is informed immediately."
                    )
                }
            )

    if policy.max_consecutive_days and days > policy.max_consecutive_days:
        raise LeaveError(
            {
                "end_date": (
                    f"{leave_type.name} allows at most {policy.max_consecutive_days} "
                    f"consecutive days; this request is {days}."
                )
            }
        )

    overlap = LeaveRequest.objects.filter(
        employee=employee, is_active=True,
        status__in=[LeaveStatus.PENDING, LeaveStatus.APPROVED],
        start_date__lte=end_date, end_date__gte=start_date,
    ).first()
    if overlap:
        raise LeaveError(
            {
                "start_date": (
                    f"This overlaps your {overlap.get_status_display().lower()} "
                    f"{overlap.leave_type.name} from {overlap.start_date:%d %b} "
                    f"to {overlap.end_date:%d %b}."
                )
            }
        )

    if policy.requires_attachment and attachment is None:
        raise LeaveError(
            {"attachment": f"{leave_type.name} requires a supporting document."}
        )
    if attachment is not None:
        validate_upload(
            attachment,
            allowed_extensions=ATTACHMENT_EXTENSIONS,
            max_bytes=ATTACHMENT_MAX_BYTES,
            subject="attachment",
        )

    # PROBATION: the person may apply — nothing blocks them — but approved
    # leave during probation is UNPAID and never touches the PL/CL balance.
    # After confirmation, the normal rules below apply untouched.
    from apps.employees.models import EmployeeStatus

    probation_unpaid = bool(
        config.probation_leave_unpaid and employee.status == EmployeeStatus.ON_PROBATION
    )

    if not probation_unpaid:
        balance = balance_for(employee, leave_type, start_date.year, actor=actor)
        if not policy.allow_negative_balance and days > balance.available:
            raise LeaveError(
                {
                    "leave_type": (
                        f"You have {balance.available} day(s) of {leave_type.name} "
                        f"available; this request needs {days}."
                    )
                }
            )

    request = LeaveRequest.objects.create(
        employee=employee,
        leave_type=leave_type,
        policy=policy,
        start_date=start_date,
        end_date=end_date,
        half_day=half_day,
        days=days,
        reason=reason.strip(),
        attachment=attachment,
        approval_band=approval_band(employee),
        approval_stage=initial_stage(employee),
        is_emergency=is_emergency,
        probation_unpaid=probation_unpaid,
    )

    if not probation_unpaid:
        balance.pending += days
        balance.save(update_fields=["pending", "updated_at"])
        LeaveTransaction.objects.create(
            employee=employee, leave_type=leave_type, year=start_date.year,
            kind=TransactionKind.PENDING_HOLD, days=-days, request=request, actor=actor,
            note=f"{start_date:%d %b}–{end_date:%d %b} submitted",
        )

    _audit(request, actor=actor, event="leave_submitted")
    transaction.on_commit(lambda: _notify_submitted(request))
    return request


# ------------------------------------------------------------------ decide


@transaction.atomic
def approve_leave(*, request: LeaveRequest, actor, note: str = "") -> LeaveRequest:
    """
    One approval step.

    At the MANAGER stage an approval FORWARDS the request to the HR Head —
    nothing is granted yet and no balance moves. Only the HR-stage approval
    (the final authority's) makes the leave real.
    """
    request = LeaveRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != LeaveStatus.PENDING:
        raise LeaveError(
            {"request": f"This request is already {request.get_status_display().lower()}."}
        )
    assert_may_decide(actor, request)

    if request.approval_stage == "manager":
        request.approval_stage = "hr"
        request.manager_decided_by = actor
        request.manager_decided_at = timezone.now()
        request.manager_note = (note or "").strip()[:255]
        request.save(
            update_fields=[
                "approval_stage", "manager_decided_by", "manager_decided_at",
                "manager_note", "updated_at",
            ]
        )
        _audit(request, actor=actor, event="leave_manager_approved")
        transaction.on_commit(lambda: _notify_forwarded(request))
        return request

    if not request.probation_unpaid:
        balance = balance_for(request.employee, request.leave_type, request.start_date.year)
        balance.pending -= request.days
        balance.used += request.days
        balance.save(update_fields=["pending", "used", "updated_at"])
        LeaveTransaction.objects.create(
            employee=request.employee, leave_type=request.leave_type,
            year=request.start_date.year, kind=TransactionKind.APPROVAL,
            days=ZERO, request=request, actor=actor,
            note="Approved: pending hold became used",
        )

    request.status = LeaveStatus.APPROVED
    request.decided_by = actor
    request.decided_at = timezone.now()
    request.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])

    _audit(request, actor=actor, event="leave_approved")
    transaction.on_commit(lambda: _notify_decided(request, "approved"))
    return request


@transaction.atomic
def reject_leave(*, request: LeaveRequest, actor, reason: str) -> LeaveRequest:
    if not (reason or "").strip():
        raise LeaveError(
            {"reason": "Rejecting leave requires a reason the employee can read."}
        )
    request = LeaveRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != LeaveStatus.PENDING:
        raise LeaveError(
            {"request": f"This request is already {request.get_status_display().lower()}."}
        )
    assert_may_decide(actor, request)

    if not request.probation_unpaid:
        balance = balance_for(request.employee, request.leave_type, request.start_date.year)
        balance.pending -= request.days
        balance.save(update_fields=["pending", "updated_at"])
        LeaveTransaction.objects.create(
            employee=request.employee, leave_type=request.leave_type,
            year=request.start_date.year, kind=TransactionKind.REJECTION_RELEASE,
            days=request.days, request=request, actor=actor, note=reason.strip()[:250],
        )

    # A manager-stage rejection ENDS the request — it never reaches HR — and
    # is recorded as the manager step's outcome as well as the final one.
    if request.approval_stage == "manager":
        request.manager_decided_by = actor
        request.manager_decided_at = timezone.now()
        request.manager_note = reason.strip()[:255]

    request.status = LeaveStatus.REJECTED
    request.decided_by = actor
    request.decided_at = timezone.now()
    request.decision_reason = reason.strip()
    request.save(
        update_fields=[
            "status", "decided_by", "decided_at", "decision_reason",
            "manager_decided_by", "manager_decided_at", "manager_note", "updated_at",
        ]
    )

    _audit(request, actor=actor, event="leave_rejected", extra={"reason": reason.strip()})
    transaction.on_commit(lambda: _notify_decided(request, "rejected"))
    return request


@transaction.atomic
def cancel_leave(*, request: LeaveRequest, actor, reason: str = "") -> LeaveRequest:
    """
    A PENDING request may always be withdrawn by its owner. An APPROVED one
    may be cancelled while it is still in the future — by its owner, or by
    someone holding the deciding authority. Either way the balance comes back
    and the ledger says so.
    """
    request = LeaveRequest.objects.select_for_update().get(pk=request.pk)
    actor_employee = getattr(actor, "employee", None)
    is_owner = actor_employee is not None and actor_employee.pk == request.employee_id

    if is_owner:
        require(actor, Resource.LEAVE_REQUEST, Action.DELETE)
    else:
        assert_may_decide(actor, request)

    if request.status == LeaveStatus.PENDING:
        pass
    elif request.status == LeaveStatus.APPROVED:
        if request.start_date <= timezone.localdate():
            raise LeaveError(
                {"request": "Leave that has started or passed cannot be cancelled."}
            )
    else:
        raise LeaveError(
            {"request": f"A {request.get_status_display().lower()} request cannot be cancelled."}
        )

    if not request.probation_unpaid:
        balance = balance_for(request.employee, request.leave_type, request.start_date.year)
        if request.status == LeaveStatus.PENDING:
            balance.pending -= request.days
        else:
            balance.used -= request.days
        balance.save(update_fields=["pending", "used", "updated_at"])
        LeaveTransaction.objects.create(
            employee=request.employee, leave_type=request.leave_type,
            year=request.start_date.year, kind=TransactionKind.CANCELLATION_REFUND,
            days=request.days, request=request, actor=actor,
            note=(reason or "Cancelled").strip()[:250],
        )

    request.status = LeaveStatus.CANCELLED
    request.decided_by = actor
    request.decided_at = timezone.now()
    if reason.strip():
        request.decision_reason = reason.strip()
    request.save(
        update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"]
    )

    _audit(request, actor=actor, event="leave_cancelled", extra={"reason": reason.strip()})
    transaction.on_commit(lambda: _notify_decided(request, "cancelled"))
    return request


# ------------------------------------------------------------ payroll seam


def get_lop_days(employee, year: int, month: int) -> Decimal:
    """
    Loss-of-pay days for the period. This is the seam payroll imports —
    payroll reads approved leave and nothing else, and cannot change it.

    Three sources, per the company policy:
      * APPROVED leave of UNPAID types (LWP);
      * APPROVED leave taken on probation — unpaid whatever the type, and
        never deducted from the PL/CL balance;
      * the month's settled short-leave conversion, where the converted days
        exceeded the paid balance.
    """
    from django.db.models import Q

    month_start = dt.date(year, month, 1)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    month_end = next_month - dt.timedelta(days=1)

    total = ZERO
    rows = LeaveRequest.objects.filter(
        Q(leave_type__is_paid=False) | Q(probation_unpaid=True),
        employee=employee, is_active=True, status=LeaveStatus.APPROVED,
        start_date__lte=month_end, end_date__gte=month_start,
    ).select_related("leave_type")
    for row in rows:
        start = max(row.start_date, month_start)
        end = min(row.end_date, month_end)
        if row.half_day:
            total += HALF
        else:
            total += working_days(employee, start, end)

    conversion = ShortLeaveConversion.objects.filter(
        employee=employee, year=year, month=month, is_active=True
    ).first()
    if conversion:
        total += conversion.lop_days
    return total


def holiday_work_dates(employee, year: int, month: int) -> list:
    """
    Approved public-holiday working days in the period — payroll's second
    read-only seam: each one earns the day AGAIN (double pay) when the
    setting says so. Empty when the setting is off.
    """
    if not LeaveSettings.get_solo().holiday_work_double_pay:
        return []
    month_start = dt.date(year, month, 1)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    return list(
        HolidayWork.objects.filter(
            employee=employee, is_active=True,
            date__gte=month_start, date__lt=next_month,
        ).values_list("date", flat=True)
    )


# ------------------------------------------------- short leave & holiday work


def record_short_leave(*, actor, employee, date, hours, out_time=None, reason="") -> ShortLeave:
    """HR records an informed early departure. One row per employee-day."""
    require(actor, Resource.LEAVE_POLICY, Action.EDIT)
    hours = Decimal(str(hours))
    if hours <= ZERO or hours > Decimal("8"):
        raise LeaveError({"hours": "Short leave is between 0 and 8 hours."})
    if ShortLeave.objects.filter(employee=employee, date=date, is_active=True).exists():
        raise LeaveError({"date": "A short leave is already recorded for this day."})
    row = ShortLeave.objects.create(
        employee=employee, date=date, hours=hours, out_time=out_time,
        reason=(reason or "").strip(), created_by=actor,
    )
    _audit_row(row, actor=actor, event="short_leave_recorded",
               extra={"date": str(date), "hours": str(hours)})
    return row


@transaction.atomic
def convert_short_leave(year: int, month: int, *, actor=None) -> int:
    """
    Settle every employee's short-leave hours for the month into days:
    `hours // short_leave_hours_per_day` full days, deducted from the paid
    balance while it lasts, loss of pay beyond it. Idempotent per
    employee-month — the unique conversion row is the guard.
    """
    config = LeaveSettings.get_solo()
    per_day = config.short_leave_hours_per_day or Decimal("3")
    month_start = dt.date(year, month, 1)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)

    converted = 0
    by_employee: dict = {}
    for row in ShortLeave.objects.filter(
        date__gte=month_start, date__lt=next_month, is_active=True
    ).select_related("employee"):
        by_employee.setdefault(row.employee, ZERO)
        by_employee[row.employee] += row.hours

    for employee, hours in by_employee.items():
        days = Decimal(int(hours / per_day))
        if days <= ZERO:
            continue
        if ShortLeaveConversion.objects.filter(
            employee=employee, year=year, month=month, is_active=True
        ).exists():
            continue

        paid_type = (
            LeaveType.objects.filter(is_active=True, is_paid=True)
            .exclude(policies__isnull=True).order_by("order").first()
        )
        deducted = ZERO
        if paid_type is not None:
            balance = balance_for(employee, paid_type, year)
            deducted = min(days, max(balance.available, ZERO))
            if deducted > ZERO:
                balance.used += deducted
                balance.save(update_fields=["used", "updated_at"])
                LeaveTransaction.objects.create(
                    employee=employee, leave_type=paid_type, year=year,
                    kind=TransactionKind.ADJUSTMENT, days=-deducted, actor=actor,
                    note=f"Short leave {month_start:%b %Y}: {hours}h = {days} day(s)",
                )

        conversion = ShortLeaveConversion.objects.create(
            employee=employee, year=year, month=month,
            hours=hours, days=days, deducted_days=deducted, lop_days=days - deducted,
        )
        _audit_row(conversion, actor=actor, event="short_leave_converted",
                   extra={"period": f"{year}-{month:02d}", "hours": str(hours),
                          "days": str(days), "lop": str(days - deducted)})
        converted += 1
    return converted


def record_holiday_work(*, actor, employee, date, note="") -> HolidayWork:
    """
    HR records an APPROVED day of work on a declared public holiday. The date
    must actually be a holiday on the employee's calendar — otherwise there is
    nothing to double-pay.
    """
    require(actor, Resource.LEAVE_POLICY, Action.EDIT)
    calendar = calendar_for(employee)
    holiday = None
    if calendar:
        holiday = Holiday.objects.filter(
            calendar=calendar, date=date, is_active=True
        ).first()
    if holiday is None:
        raise LeaveError(
            {"date": f"{date:%d %b %Y} is not a declared public holiday on this "
                     f"employee's calendar."}
        )
    if HolidayWork.objects.filter(employee=employee, date=date, is_active=True).exists():
        raise LeaveError({"date": "Holiday work is already recorded for this day."})
    row = HolidayWork.objects.create(
        employee=employee, date=date, holiday_name=holiday.name,
        approved_by=actor, note=(note or "").strip(), created_by=actor,
    )
    _audit_row(row, actor=actor, event="holiday_work_recorded",
               extra={"date": str(date), "holiday": holiday.name})
    return row


# --------------------------------------------------- HR review: patterns


def leave_patterns(year: int) -> list[dict]:
    """
    The signals HR reviews: Monday/Friday clustering, last-minute requests,
    emergencies, and short-leave hours — per employee, for the year. Numbers
    only; judgement stays human.
    """
    rows: dict = {}
    for request in LeaveRequest.objects.filter(
        start_date__year=year, is_active=True,
    ).exclude(status=LeaveStatus.CANCELLED).select_related("employee"):
        entry = rows.setdefault(request.employee_id, {
            "employee_id": str(request.employee_id),
            "employee_name": request.employee.full_name,
            "employee_code": request.employee.employee_code,
            "requests": 0, "monday_friday": 0, "last_minute": 0,
            "emergency": 0, "short_leave_hours": ZERO,
        })
        entry["requests"] += 1
        if request.start_date.weekday() in (0, 4) or request.end_date.weekday() in (0, 4):
            entry["monday_friday"] += 1
        if (request.start_date - request.created_at.date()).days < 3:
            entry["last_minute"] += 1
        if request.is_emergency:
            entry["emergency"] += 1

    for short in ShortLeave.objects.filter(date__year=year, is_active=True):
        entry = rows.get(short.employee_id)
        if entry:
            entry["short_leave_hours"] += short.hours

    result = sorted(rows.values(), key=lambda r: -r["requests"])
    for entry in result:
        entry["short_leave_hours"] = str(entry["short_leave_hours"])
    return result


# --------------------------------------------------- absence flag (guarded)


def flag_unreported_absences() -> int:
    """
    Uninformed absence for N consecutive working days → a HIGH notification
    to HR flagging POTENTIAL abandonment of service. Flag only: the policy is
    explicit that nobody is terminated automatically.

    Attendance is not built in this system yet, so this activates the day an
    `apps.attendance.services.was_present(employee, date)` exists; until then
    it is a quiet no-op rather than a false alarm for every employee.
    """
    try:
        from apps.attendance.services import was_present  # type: ignore
    except ImportError:
        return 0

    from apps.employees.models import Employee, EmployeeStatus
    from apps.notifications.events import _users_holding
    from apps.notifications.models import NotificationKind, Priority
    from apps.notifications.services import notify_many
    from core.access import Scope

    config = LeaveSettings.get_solo()
    today = timezone.localdate()
    flagged = 0
    for employee in Employee.objects.filter(is_active=True).exclude(
        status__in=[EmployeeStatus.EXITED, EmployeeStatus.TERMINATED]
    ):
        streak, day = 0, today - dt.timedelta(days=1)
        while streak < config.absence_flag_days:
            if working_days(employee, day, day) <= ZERO:
                day -= dt.timedelta(days=1)
                continue  # weekly off / holiday — not an absence
            on_leave = LeaveRequest.objects.filter(
                employee=employee, status=LeaveStatus.APPROVED, is_active=True,
                start_date__lte=day, end_date__gte=day,
            ).exists()
            if on_leave or was_present(employee, day):
                break
            streak += 1
            day -= dt.timedelta(days=1)
        if streak >= config.absence_flag_days:
            notify_many(
                recipients=_users_holding(Resource.LEAVE_REQUEST, Action.APPROVE,
                                          scope_at_least=Scope.ALL),
                kind=NotificationKind.LEAVE_SUBMITTED,
                title=f"Uninformed absence: {employee.full_name}",
                body=(f"{employee.full_name} ({employee.employee_code}) has been absent "
                      f"without information for {streak} consecutive working days. "
                      f"Review for potential abandonment of service — no automatic "
                      f"action has been taken."),
                link_url="/employees",
                entity=employee,
                priority=Priority.HIGH,
                dedupe_key=f"absence-flag:{employee.pk}:{today.isoformat()}",
            )
            flagged += 1
    return flagged


def _audit_row(row, *, actor, event: str, extra: dict | None = None) -> None:
    from apps.audit.events import record_event

    record_event(
        row, actor=actor, entity_type=f"leave.{type(row).__name__}", verb="update",
        resource=Resource.LEAVE_REQUEST,
        after={"event": event, **(extra or {})},
    )


# ------------------------------------------------------------ year rollover


@transaction.atomic
def annual_rollover(*, year: int, actor=None) -> int:
    """
    Carry each employee's remaining balance into `year`, capped by the
    policy's limit. Run once at the turn of the year; idempotent per row.
    """
    moved = 0
    for balance in LeaveBalance.objects.filter(year=year - 1, is_active=True).select_related(
        "employee", "leave_type"
    ):
        policy = policy_for(balance.employee, balance.leave_type)
        if policy is None or not policy.carry_forward:
            continue
        remaining = balance.available
        if remaining <= ZERO:
            continue
        carry = min(remaining, policy.carry_forward_limit or remaining)
        target = balance_for(balance.employee, balance.leave_type, year, actor=actor)
        if target.carried_forward:
            continue  # already rolled
        target.carried_forward = carry
        target.save(update_fields=["carried_forward", "updated_at"])
        LeaveTransaction.objects.create(
            employee=balance.employee, leave_type=balance.leave_type, year=year,
            kind=TransactionKind.CARRY_FORWARD, days=carry, actor=actor,
            note=f"Carried forward from {year - 1}",
        )
        moved += 1
    return moved


# -------------------------------------------------------------- notifications


def _admin_users():
    from apps.accounts.models import User

    return User.objects.filter(
        is_active=True, user_roles__is_active=True, user_roles__role__code=RoleCode.ADMIN
    ).distinct()


def _hr_users():
    from apps.accounts.models import User

    return User.objects.filter(
        is_active=True, user_roles__is_active=True,
        user_roles__role__code=RoleCode.HR_HEAD,
    ).distinct()


def _request_summary(request: LeaveRequest) -> str:
    return (
        f"{request.leave_type.name} · {request.start_date:%d %b}–{request.end_date:%d %b} "
        f"· {request.days} day(s)"
    )


def _notify_submitted(request: LeaveRequest) -> None:
    from apps.notifications.models import NotificationKind, Priority
    from apps.notifications.services import notify, notify_many

    title = f"Leave request: {request.employee.full_name}"
    body = _request_summary(request)

    # The first approver in the chain is asked to act; nobody else yet.
    if request.approval_stage == "manager":
        manager = request.employee.reporting_manager
        if manager is not None and manager.user_id:
            notify(
                recipient=manager.user,
                kind=NotificationKind.LEAVE_SUBMITTED,
                title=title,
                body=body + " · Your approval is the first step.",
                link_url="/leave",
                entity=request,
                priority=Priority.NORMAL,
                dedupe_key=f"leave-submitted:{request.pk}",
            )
        return

    approvers = _admin_users() if request.approval_band == "admin" else _hr_users()
    notify_many(
        recipients=approvers,
        kind=NotificationKind.LEAVE_SUBMITTED,
        title=title, body=body, link_url="/leave",
        entity=request, priority=Priority.NORMAL,
        dedupe_key=f"leave-submitted:{request.pk}",
    )


def _notify_forwarded(request: LeaveRequest) -> None:
    """The manager approved: the HR Head is now asked, the employee told."""
    from apps.notifications.models import NotificationKind, Priority
    from apps.notifications.services import notify, notify_many

    approvers = _admin_users() if request.approval_band == "admin" else _hr_users()
    notify_many(
        recipients=approvers,
        kind=NotificationKind.LEAVE_SUBMITTED,
        title=f"Leave request (manager approved): {request.employee.full_name}",
        body=_request_summary(request) + " · Forwarded by the reporting manager.",
        link_url="/leave",
        entity=request, priority=Priority.NORMAL,
        dedupe_key=f"leave-forwarded:{request.pk}",
    )
    if request.employee.user_id:
        notify(
            recipient=request.employee.user,
            kind=NotificationKind.LEAVE_SUBMITTED,
            title="Your leave request moved forward",
            body=_request_summary(request) + " · Approved by your manager; awaiting the HR Head.",
            link_url="/leave",
            entity=request,
            dedupe_key=f"leave-forwarded-fyi:{request.pk}",
        )


def _notify_decided(request: LeaveRequest, outcome: str) -> None:
    from apps.notifications.models import NotificationKind, Priority
    from apps.notifications.services import notify

    if request.employee.user_id is None:
        return
    body = (
        f"{request.leave_type.name} · {request.start_date:%d %b}–{request.end_date:%d %b} "
        f"· {request.days} day(s)"
    )
    if request.decision_reason and outcome == "rejected":
        body += f"\nReason: {request.decision_reason}"
    notify(
        recipient=request.employee.user,
        kind=NotificationKind.LEAVE_DECIDED,
        title=f"Your leave was {outcome}",
        body=body,
        link_url="/leave",
        entity=request,
        priority=Priority.HIGH if outcome == "rejected" else Priority.NORMAL,
        dedupe_key=f"leave-{outcome}:{request.pk}",
    )


# -------------------------------------------------------------------- audit


def _audit(request: LeaveRequest, *, actor, event: str, extra: dict | None = None) -> None:
    from apps.audit.events import record_event

    record_event(
        request,
        actor=actor,
        entity_type="leave.LeaveRequest",
        verb="update" if event != "leave_submitted" else "create",
        resource=Resource.LEAVE_REQUEST,
        after={
            "event": event,
            "employee": request.employee.employee_code,
            "leave_type": request.leave_type.code,
            "from": request.start_date.isoformat(),
            "to": request.end_date.isoformat(),
            "days": str(request.days),
            "status": request.status,
            "band": request.approval_band,
            **(extra or {}),
        },
    )
