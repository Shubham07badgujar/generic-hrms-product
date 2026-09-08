"""
Custom package schedules and deferred releases.

The package is the AGREEMENT; payroll remains the only thing that PAYS.
Everything here either records the agreement (periods, deferrals, statuses),
validates it to the rupee, or — at the single crossing point — turns an
approved release into a DEFERRED_RELEASE payroll adjustment, after which the
ordinary run machinery (separate payslip line, statutory treatment from the
configured rules, locking, reversal) applies unchanged.

Double counting is prevented structurally: a deferral can hold at most one
release adjustment, monthly pay flows only from salary structures, and the
dashboard's "paid so far" is measured from issued payslips, never assumed.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import Resource

from ..models import (
    AdjustmentKind,
    AdjustmentStatus,
    DeferralStatus,
    EmployeePackage,
    PackageDeferral,
    PackagePeriod,
    PackageStatus,
    PayrollAdjustment,
    PayrollRunStatus,
    Payslip,
    ReleaseCondition,
)
from .audit import audit_event

ZERO = Decimal("0.00")


class PackageError(ValidationError):
    """A refused package operation."""


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _add_months(day: dt.date, months: int) -> dt.date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    import calendar

    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------- validation


def allocation_gap(package: EmployeePackage) -> Decimal:
    """total - allocated. Positive = unallocated, negative = over-allocated."""
    return package.total_amount - package.allocated_total


def assert_fully_allocated(package: EmployeePackage) -> None:
    gap = allocation_gap(package)
    if gap < ZERO:
        raise PackageError(
            {"total_amount": "Package allocation exceeds total package amount."}
        )
    if gap > ZERO:
        raise PackageError(
            {"total_amount": (
                f"{gap:,.2f} of the total package has not been allocated. "
                f"Allocate it to a period or a deferred amount — or adjust the "
                f"total — before activating."
            )}
        )


# ------------------------------------------------------------------- writes


@transaction.atomic
def save_package(*, actor, employee, data: dict, package: EmployeePackage | None = None):
    """
    Create or edit a package with its periods and deferrals, fully audited.

    Periods and deferrals are replaced wholesale from the payload — the
    schedule is edited as one document. A deferral that has already been
    approved or paid is never touched by a re-save; it is carried forward.
    """
    before = None
    if package is not None:
        before = {
            "total_amount": str(package.total_amount),
            "periods": [
                {"label": p.label, "amount": str(p.amount)} for p in package.periods.all()
            ],
            "deferrals": [
                {"label": d.label, "amount": str(d.amount), "status": d.status}
                for d in package.deferrals.all()
            ],
        }
        if package.status == PackageStatus.CANCELLED:
            raise PackageError({"status": "A cancelled package cannot be edited."})

    fields = {
        "total_amount": _money(data["total_amount"]),
        "package_type": data.get("package_type", "period_wise"),
        "start_date": data["start_date"],
        "end_date": data["end_date"],
        "notes": data.get("notes", ""),
        "bond_start_date": data.get("bond_start_date"),
        "bond_end_date": data.get("bond_end_date"),
        "bond_required_months": data.get("bond_required_months"),
    }
    if package is None:
        package = EmployeePackage.objects.create(
            employee=employee, created_by=actor, updated_by=actor, **fields
        )
    else:
        for key, value in fields.items():
            setattr(package, key, value)
        package.updated_by = actor
        package.save(update_fields=[*fields, "updated_by", "updated_at"])

    # Replace the schedule. Decided deferrals survive untouched.
    for period in package.periods.all():
        period.delete(hard=True)
    for order, row in enumerate(data.get("periods", []), start=1):
        PackagePeriod.objects.create(
            package=package, order=order,
            label=row.get("label") or f"Period {order}",
            start_date=row["start_date"], end_date=row["end_date"],
            amount=_money(row["amount"]),
            created_by=actor, updated_by=actor,
        )

    untouchable = {
        DeferralStatus.APPROVED, DeferralStatus.PAID, DeferralStatus.REJECTED,
    }
    for deferral in package.deferrals.all():
        if deferral.status not in untouchable:
            deferral.delete(hard=True)
    for row in data.get("deferrals", []):
        condition_type = row.get("condition_type", ReleaseCondition.AFTER_MONTHS)
        PackageDeferral.objects.create(
            package=package,
            label=row.get("label") or "Deferred amount",
            amount=_money(row["amount"]),
            condition_type=condition_type,
            condition_months=row.get("condition_months"),
            eligible_on=row.get("eligible_on"),
            condition_note=row.get("condition_note", ""),
            created_by=actor, updated_by=actor,
        )

    # Editing an ACTIVE schedule must keep it balanced — a draft may be
    # work-in-progress, a live agreement may not.
    if package.status in (PackageStatus.ACTIVE, PackageStatus.ON_HOLD):
        package = EmployeePackage.objects.get(pk=package.pk)
        assert_fully_allocated(package)
        _compute_eligibility_dates(package, actor=actor)

    audit_event(
        package, actor=actor, entity_type="EmployeePackage",
        verb="update" if before else "create",
        resource=Resource.PACKAGE,
        before=before,
        after={
            "employee": package.employee.employee_code,
            "total_amount": str(package.total_amount),
            "periods": [
                {"label": p.label, "amount": str(p.amount)} for p in package.periods.all()
            ],
            "deferrals": [
                {"label": d.label, "amount": str(d.amount), "status": d.status}
                for d in package.deferrals.all()
            ],
        },
        reason=data.get("reason", ""),
    )
    return package


def _compute_eligibility_dates(package: EmployeePackage, *, actor) -> None:
    for deferral in package.deferrals.filter(status=DeferralStatus.PENDING):
        eligible = deferral.eligible_on
        if deferral.condition_type == ReleaseCondition.AFTER_MONTHS and deferral.condition_months:
            eligible = _add_months(package.start_date, deferral.condition_months)
        elif deferral.condition_type == ReleaseCondition.BOND_COMPLETION and package.bond_end_date:
            eligible = package.bond_end_date
        elif deferral.condition_type == ReleaseCondition.MANUAL:
            eligible = None
        if eligible != deferral.eligible_on:
            deferral.eligible_on = eligible
            deferral.updated_by = actor
            deferral.save(update_fields=["eligible_on", "updated_by", "updated_at"])


@transaction.atomic
def activate(*, actor, package: EmployeePackage) -> EmployeePackage:
    """DRAFT -> ACTIVE, only when allocated to the rupee."""
    if package.status not in (PackageStatus.DRAFT, PackageStatus.ON_HOLD):
        raise PackageError({"status": f"A {package.get_status_display()} package cannot be activated."})
    assert_fully_allocated(package)
    if EmployeePackage.objects.filter(
        employee=package.employee, is_active=True,
        status__in=(PackageStatus.ACTIVE, PackageStatus.ON_HOLD),
    ).exclude(pk=package.pk).exists():
        raise PackageError({
            "employee": "This employee already has a live package. Revise it "
                        "(which keeps its history) instead of activating a second one."
        })

    _compute_eligibility_dates(package, actor=actor)
    package.status = PackageStatus.ACTIVE
    package.activated_by = actor
    package.activated_at = timezone.now()
    package.save(update_fields=["status", "activated_by", "activated_at", "updated_at"])
    audit_event(
        package, actor=actor, entity_type="EmployeePackage", verb="approve",
        resource=Resource.PACKAGE,
        after={"event": "package_activated", "total_amount": str(package.total_amount)},
    )
    refresh_eligibility(package)
    return package


@transaction.atomic
def set_status(*, actor, package: EmployeePackage, status: str, reason: str = "") -> EmployeePackage:
    """Hold / resume / cancel, with the reason on record."""
    allowed = {PackageStatus.ON_HOLD, PackageStatus.CANCELLED, PackageStatus.ACTIVE,
               PackageStatus.COMPLETED}
    if status not in allowed:
        raise PackageError({"status": f"'{status}' is not a settable status."})
    if status == PackageStatus.CANCELLED and not reason.strip():
        raise PackageError({"reason": "Cancelling a package requires a reason."})
    before = package.status
    package.status = status
    package.updated_by = actor
    package.save(update_fields=["status", "updated_by", "updated_at"])
    audit_event(
        package, actor=actor, entity_type="EmployeePackage", verb="update",
        resource=Resource.PACKAGE,
        before={"status": before}, after={"status": status}, reason=reason,
    )
    return package


@transaction.atomic
def revise(*, actor, package: EmployeePackage, data: dict) -> EmployeePackage:
    """
    A new schedule superseding the current one. The old package is closed and
    kept as history — never edited in place, exactly like salary structures.
    """
    set_status(actor=actor, package=package, status=PackageStatus.CANCELLED,
               reason=data.get("reason", "Superseded by a revised package schedule."))
    replacement = save_package(actor=actor, employee=package.employee, data=data)
    replacement.supersedes = package
    replacement.save(update_fields=["supersedes", "updated_at"])
    return replacement


# ------------------------------------------------------------- eligibility


def refresh_eligibility(package: EmployeePackage | None = None) -> int:
    """
    PENDING deferrals whose date has arrived become ELIGIBLE — visibly, and
    on the audit trail. Never pays anything.
    """
    today = timezone.localdate()
    rows = PackageDeferral.objects.filter(
        status=DeferralStatus.PENDING, eligible_on__isnull=False, eligible_on__lte=today,
        package__status=PackageStatus.ACTIVE, is_active=True,
    )
    if package is not None:
        rows = rows.filter(package=package)
    changed = 0
    for deferral in rows.select_related("package__employee"):
        deferral.status = DeferralStatus.ELIGIBLE
        deferral.save(update_fields=["status", "updated_at"])
        audit_event(
            deferral, actor=None, entity_type="PackageDeferral", verb="update",
            resource=Resource.PACKAGE,
            after={"event": "deferral_eligible", "amount": str(deferral.amount),
                   "employee": deferral.package.employee.employee_code,
                   "eligible_on": str(deferral.eligible_on)},
        )
        changed += 1
    return changed


# ------------------------------------------------------------------ release


@transaction.atomic
def decide_release(
    *, actor, deferral: PackageDeferral, decision: str,
    period_year: int | None = None, period_month: int | None = None,
    reason: str = "",
) -> PackageDeferral:
    """
    approve / reject / hold an eligible deferral.

    Approval creates the DEFERRED_RELEASE adjustment for the chosen payroll
    period — already APPROVED (this decision IS the approval), so the run
    picks it up as its own payslip line. One adjustment per deferral, ever.
    """
    if decision not in ("approve", "reject", "hold"):
        raise PackageError({"decision": "Decision must be approve, reject or hold."})
    if deferral.status not in (DeferralStatus.ELIGIBLE, DeferralStatus.ON_HOLD,
                               DeferralStatus.PENDING if decision != "approve" else DeferralStatus.ELIGIBLE):
        if not (decision == "approve" and deferral.status == DeferralStatus.ELIGIBLE):
            raise PackageError({
                "status": f"A {deferral.get_status_display()} deferral cannot be {decision}d "
                          f"here. Only eligible (or held) amounts take a decision."
            })
    before = deferral.status

    if decision == "approve":
        if deferral.released_in_adjustment_id:
            raise PackageError({"deferral": "This amount already has a release adjustment."})
        if not period_year or not period_month:
            raise PackageError({"period": "Choose the payroll period the release is paid in."})
        adjustment = PayrollAdjustment.objects.create(
            employee=deferral.package.employee,
            kind=AdjustmentKind.DEFERRED_RELEASE,
            label=f"Deferred Package Release — {deferral.label}",
            amount=deferral.amount,
            period_year=period_year, period_month=period_month,
            is_taxable=True,
            status=AdjustmentStatus.APPROVED,
            approved_by=actor, approved_at=timezone.now(),
            source_ref=f"package:{deferral.package_id}",
            notes=reason,
            created_by=actor, updated_by=actor,
        )
        deferral.released_in_adjustment = adjustment
        deferral.status = DeferralStatus.APPROVED
    elif decision == "reject":
        if not reason.strip():
            raise PackageError({"reason": "Rejecting a release requires a reason."})
        deferral.status = DeferralStatus.REJECTED
    else:
        deferral.status = DeferralStatus.ON_HOLD

    deferral.decided_by = actor
    deferral.decided_at = timezone.now()
    deferral.decision_reason = reason
    deferral.updated_by = actor
    deferral.save(update_fields=[
        "status", "decided_by", "decided_at", "decision_reason",
        "released_in_adjustment", "updated_by", "updated_at",
    ])
    audit_event(
        deferral, actor=actor, entity_type="PackageDeferral", verb="approve",
        resource=Resource.PACKAGE,
        before={"status": before},
        after={"event": f"release_{decision}", "amount": str(deferral.amount),
               "employee": deferral.package.employee.employee_code,
               "period": f"{period_year}-{period_month}" if decision == "approve" else None},
        reason=reason,
    )
    return deferral


@transaction.atomic
def reschedule(*, actor, deferral: PackageDeferral, eligible_on: dt.date, reason: str) -> PackageDeferral:
    if deferral.status in (DeferralStatus.APPROVED, DeferralStatus.PAID):
        raise PackageError({"status": "A released amount cannot be rescheduled."})
    if not reason.strip():
        raise PackageError({"reason": "Changing a release date requires a reason."})
    before = str(deferral.eligible_on)
    deferral.eligible_on = eligible_on
    if deferral.status == DeferralStatus.ELIGIBLE and eligible_on > timezone.localdate():
        deferral.status = DeferralStatus.PENDING
    deferral.updated_by = actor
    deferral.save(update_fields=["eligible_on", "status", "updated_by", "updated_at"])
    audit_event(
        deferral, actor=actor, entity_type="PackageDeferral", verb="update",
        resource=Resource.PACKAGE,
        before={"eligible_on": before}, after={"eligible_on": str(eligible_on)},
        reason=reason,
    )
    return deferral


# ------------------------------------------------------------------ summary


def summary(package: EmployeePackage) -> dict:
    """
    The dashboard numbers, measured — never assumed.

    "Paid so far" is the SUM of issued payslips (approved or paid runs)
    inside the package window, so mid-month joins, LWP and absents are
    reflected automatically. Deferred figures come from the deferrals'
    own lifecycle.
    """
    paid = ZERO
    slips = Payslip.objects.filter(
        employee=package.employee, is_active=True,
        payroll_run__status__in=(PayrollRunStatus.APPROVED, PayrollRunStatus.PAID),
        payroll_run__is_active=True,
    ).select_related("payroll_run")
    for slip in slips:
        run = slip.payroll_run
        period = dt.date(run.period_year, run.period_month, 1)
        if package.start_date.replace(day=1) <= period <= package.end_date:
            paid += slip.gross_earnings

    deferred_total = ZERO
    released_total = ZERO
    next_release = None
    for deferral in package.deferrals.filter(is_active=True):
        if deferral.status in (DeferralStatus.CANCELLED, DeferralStatus.REJECTED):
            continue
        deferred_total += deferral.amount
        if deferral.effective_status in (DeferralStatus.APPROVED, DeferralStatus.PAID):
            released_total += deferral.amount
        elif deferral.eligible_on and (next_release is None or deferral.eligible_on < next_release):
            next_release = deferral.eligible_on

    return {
        "paid_so_far": str(paid),
        "deferred_total": str(deferred_total),
        "released_total": str(released_total),
        "remaining_deferred": str(deferred_total - released_total),
        "next_release_date": str(next_release) if next_release else None,
        "allocation_gap": str(allocation_gap(package)),
    }
