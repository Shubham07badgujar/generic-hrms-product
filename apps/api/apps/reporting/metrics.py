"""
Metric definitions.

Every function here takes `(ctx, params)` where `ctx` carries the scoped
querysets — already narrowed by `scope_queryset()` for the metric's declared
resource — and returns `MetricPoint`s.

There is no role branching. A Department Head calling `hr.headcount` runs the
identical function the CEO does; the difference is entirely in what
`ctx.employees()` returned, which the access engine decided. That is why
"employees see their own metrics" needs no code here at all: at SELF scope the
same queryset contains one row.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Sum

from core.access.catalog import Action, Resource

from .registry import MetricPoint, metric

# ===========================================================================
# Workforce
# ===========================================================================


@metric(
    "hr.headcount",
    label="Headcount",
    description="People currently employed and not exited.",
    resource=Resource.EMPLOYEE,
    groupings=("department", "status", "location"),
)
def headcount(ctx, params) -> list[MetricPoint]:
    from apps.employees.models import EmployeeStatus

    employees = ctx.employees().exclude(status=EmployeeStatus.EXITED)
    group = params.get("group_by")

    if group == "department":
        return _group_count(employees, "department__name", "department_id")
    if group == "status":
        return _group_count(employees, "status", "status")
    if group == "location":
        return _group_count(employees, "location__name", "location_id")

    return [MetricPoint(key="total", label="Headcount", value=employees.count())]


@metric(
    "hr.joiners",
    label="Joiners",
    description="People who joined within the period.",
    resource=Resource.EMPLOYEE,
    supports_range=True,
    groupings=("department",),
)
def joiners(ctx, params) -> list[MetricPoint]:
    employees = ctx.employees().filter(
        date_of_joining__gte=params["start"], date_of_joining__lte=params["end"]
    )
    if params.get("group_by") == "department":
        return _group_count(employees, "department__name", "department_id")
    return [MetricPoint(key="total", label="Joiners", value=employees.count())]


@metric(
    "hr.leavers",
    label="Leavers",
    description="People who left within the period.",
    resource=Resource.EMPLOYEE,
    supports_range=True,
    groupings=("department",),
)
def leavers(ctx, params) -> list[MetricPoint]:
    employees = ctx.employees().filter(
        date_of_exit__gte=params["start"], date_of_exit__lte=params["end"]
    )
    if params.get("group_by") == "department":
        return _group_count(employees, "department__name", "department_id")
    return [MetricPoint(key="total", label="Leavers", value=employees.count())]


@metric(
    "hr.attrition_rate",
    label="Attrition rate",
    description="Leavers as a percentage of average headcount over the period.",
    resource=Resource.EMPLOYEE,
    unit="percent",
    supports_range=True,
)
def attrition_rate(ctx, params) -> list[MetricPoint]:
    """
    Leavers ÷ average headcount.

    Average of opening and closing headcount, not closing alone: a team that
    halved would otherwise report an attrition rate above 100%, which is
    arithmetically defensible and useless to read.
    """
    from apps.employees.models import EmployeeStatus

    employees = ctx.employees()
    start, end = params["start"], params["end"]

    left = employees.filter(date_of_exit__gte=start, date_of_exit__lte=end).count()

    closing = employees.exclude(status=EmployeeStatus.EXITED).filter(
        date_of_joining__lte=end
    ).count()
    opening = employees.filter(date_of_joining__lt=start).exclude(
        date_of_exit__lt=start
    ).count()

    average = (opening + closing) / 2 if (opening or closing) else 0
    rate = round((left / average) * 100, 2) if average else 0.0

    return [
        MetricPoint(
            key="attrition",
            label="Attrition rate",
            value=rate,
            context={"leavers": left, "average_headcount": round(average, 1)},
        )
    ]


@metric(
    "hr.probation_pending",
    label="Probation reviews pending",
    description="Probation decisions HR has not yet made. Never resolves on its own.",
    resource=Resource.PROBATION_REVIEW,
)
def probation_pending(ctx, params) -> list[MetricPoint]:
    from apps.employees.models import ProbationDecision

    reviews = ctx.scoped(
        _all("employees.ProbationReview"), Resource.PROBATION_REVIEW
    ).filter(decision=ProbationDecision.PENDING)

    overdue = reviews.filter(probation_end_date__lt=dt.date.today()).count()
    return [
        MetricPoint(
            key="pending",
            label="Pending",
            value=reviews.count(),
            context={"overdue": overdue},
        )
    ]


@metric(
    "hr.headcount_trend",
    label="Headcount trend",
    description="Headcount at the end of each month in the period.",
    resource=Resource.EMPLOYEE,
    shape="series",
    supports_range=True,
    snapshotable=True,
)
def headcount_trend(ctx, params) -> list[MetricPoint]:
    employees = ctx.employees()
    points = []
    for month_end in _month_ends(params["start"], params["end"]):
        count = (
            employees.filter(date_of_joining__lte=month_end)
            .exclude(date_of_exit__lt=month_end)
            .count()
        )
        points.append(
            MetricPoint(key=month_end.isoformat(), label=f"{month_end:%b %Y}", value=count)
        )
    return points


# ===========================================================================
# Recruitment
# ===========================================================================


@metric(
    "recruitment.open_positions",
    label="Open positions",
    description="Published job openings still accepting applications.",
    resource=Resource.JOB_OPENING,
    groupings=("department",),
)
def open_positions(ctx, params) -> list[MetricPoint]:
    from apps.recruitment.models import JobStatus

    jobs = ctx.scoped(_all("recruitment.JobOpening"), Resource.JOB_OPENING).filter(
        status=JobStatus.PUBLISHED
    )
    if params.get("group_by") == "department":
        return _group_count(jobs, "department__name", "department_id")
    return [MetricPoint(key="total", label="Open positions", value=jobs.count())]


@metric(
    "recruitment.pipeline",
    label="Hiring funnel",
    description="Open applications by the stage they are sitting at.",
    resource=Resource.APPLICATION,
    shape="breakdown",
)
def pipeline(ctx, params) -> list[MetricPoint]:
    applications = ctx.scoped(
        _all("recruitment.Application"), Resource.APPLICATION
    ).filter(current_stage__isnull=False)

    rows = (
        applications.values("current_stage__name", "current_stage__order")
        .annotate(count=Count("id"))
        .order_by("current_stage__order")
    )
    return [
        MetricPoint(
            key=str(row["current_stage__order"]),
            label=row["current_stage__name"] or "—",
            value=row["count"],
        )
        for row in rows
    ]


@metric(
    "recruitment.time_to_hire",
    label="Time to hire",
    description="Average days from application to selection, for hires in the period.",
    resource=Resource.APPLICATION,
    unit="days",
    supports_range=True,
)
def time_to_hire(ctx, params) -> list[MetricPoint]:
    from apps.recruitment.models import ApplicationStatus

    selected = ctx.scoped(_all("recruitment.Application"), Resource.APPLICATION).filter(
        status=ApplicationStatus.SELECTED,
        updated_at__date__gte=params["start"],
        updated_at__date__lte=params["end"],
    )

    days, counted = 0, 0
    for application in selected.only("created_at", "updated_at"):
        days += (application.updated_at - application.created_at).days
        counted += 1

    return [
        MetricPoint(
            key="average",
            label="Average days to hire",
            value=round(days / counted, 1) if counted else 0,
            context={"hires": counted},
        )
    ]


@metric(
    "recruitment.decisions_awaiting_hr",
    label="Awaiting HR decision",
    description="Applications the department has reviewed, waiting on HR to make it final.",
    resource=Resource.APPLICATION,
)
def decisions_awaiting_hr(ctx, params) -> list[MetricPoint]:
    applications = ctx.scoped(
        _all("recruitment.Application"), Resource.APPLICATION
    ).filter(current_stage__is_final_hr_decision=True)

    return [MetricPoint(key="total", label="Awaiting HR", value=applications.count())]


# ===========================================================================
# Finance
# ===========================================================================


@metric(
    "finance.payroll_cost",
    label="Payroll cost",
    description="Gross earnings plus employer contributions for approved runs in the period.",
    resource=Resource.PAYSLIP,
    unit="currency",
    supports_range=True,
    groupings=("department",),
)
def payroll_cost(ctx, params) -> list[MetricPoint]:
    payslips = _payslips_in_period(ctx, params)

    if params.get("group_by") == "department":
        rows = (
            payslips.values("employee__department__name")
            .annotate(gross=Sum("gross_earnings"), employer=Sum("employer_contributions"))
            .order_by("-gross")
        )
        return [
            MetricPoint(
                key=row["employee__department__name"] or "unassigned",
                label=row["employee__department__name"] or "Unassigned",
                value=float((row["gross"] or 0) + (row["employer"] or 0)),
            )
            for row in rows
        ]

    totals = payslips.aggregate(
        gross=Sum("gross_earnings"), employer=Sum("employer_contributions")
    )
    gross = totals["gross"] or Decimal("0")
    employer = totals["employer"] or Decimal("0")

    return [
        MetricPoint(
            key="total",
            label="Total cost to company",
            value=float(gross + employer),
            context={"gross": float(gross), "employer_contributions": float(employer)},
        )
    ]


@metric(
    "finance.net_pay",
    label="Net pay",
    description="Net pay disbursed for approved runs in the period.",
    resource=Resource.PAYSLIP,
    unit="currency",
    supports_range=True,
)
def net_pay(ctx, params) -> list[MetricPoint]:
    total = _payslips_in_period(ctx, params).aggregate(total=Sum("net_pay"))["total"]
    return [MetricPoint(key="total", label="Net pay", value=float(total or 0))]


@metric(
    "finance.statutory_liability",
    label="Statutory liability",
    description="Employee and employer statutory amounts by statute for the period.",
    resource=Resource.PAYSLIP,
    unit="currency",
    shape="breakdown",
    supports_range=True,
)
def statutory_liability(ctx, params) -> list[MetricPoint]:
    from apps.payroll.models import StatutoryContribution

    payslips = _payslips_in_period(ctx, params)
    rows = (
        StatutoryContribution.objects.filter(payslip__in=payslips, applied=True)
        .values("kind")
        .annotate(employee=Sum("employee_amount"), employer=Sum("employer_amount"))
        .order_by("kind")
    )
    return [
        MetricPoint(
            key=row["kind"],
            label=row["kind"].upper(),
            value=float((row["employee"] or 0) + (row["employer"] or 0)),
            context={
                "employee": float(row["employee"] or 0),
                "employer": float(row["employer"] or 0),
            },
        )
        for row in rows
    ]


@metric(
    "finance.payroll_cost_trend",
    label="Payroll cost trend",
    description="Cost to company per month across the period.",
    resource=Resource.PAYSLIP,
    unit="currency",
    shape="series",
    supports_range=True,
    snapshotable=True,
)
def payroll_cost_trend(ctx, params) -> list[MetricPoint]:
    from apps.payroll.models import PayrollRunStatus

    payslips = ctx.scoped(_all("payroll.Payslip"), Resource.PAYSLIP).filter(
        payroll_run__status__in=[PayrollRunStatus.APPROVED, PayrollRunStatus.PAID]
    )
    rows = (
        payslips.values("payroll_run__period_year", "payroll_run__period_month")
        .annotate(gross=Sum("gross_earnings"), employer=Sum("employer_contributions"))
        .order_by("payroll_run__period_year", "payroll_run__period_month")
    )
    return [
        MetricPoint(
            key=f"{row['payroll_run__period_year']}-{row['payroll_run__period_month']:02d}",
            label=f"{row['payroll_run__period_year']}-{row['payroll_run__period_month']:02d}",
            value=float((row["gross"] or 0) + (row["employer"] or 0)),
        )
        for row in rows
    ]


# ===========================================================================
# Operations
# ===========================================================================


@metric(
    "operations.assets_allocated",
    label="Assets allocated",
    description="Company property currently issued to people.",
    resource=Resource.ASSET_ALLOCATION,
)
def assets_allocated(ctx, params) -> list[MetricPoint]:
    from apps.assets.models import AllocationStatus

    allocations = ctx.scoped(
        _all("assets.AssetAllocation"), Resource.ASSET_ALLOCATION
    ).filter(status=AllocationStatus.ACTIVE)

    return [MetricPoint(key="total", label="Allocated", value=allocations.count())]


@metric(
    "operations.exits_in_progress",
    label="Exits in progress",
    description="People working notice or in clearance.",
    resource=Resource.OFFBOARDING,
    groupings=("stage",),
)
def exits_in_progress(ctx, params) -> list[MetricPoint]:
    from apps.offboarding.models import CLOSED_STAGES

    exits = ctx.scoped(_all("offboarding.ExitWorkflow"), Resource.OFFBOARDING).exclude(
        stage__in=CLOSED_STAGES
    )
    if params.get("group_by") == "stage":
        return _group_count(exits, "stage", "stage")
    return [MetricPoint(key="total", label="Exits in progress", value=exits.count())]


@metric(
    "operations.onboarding_in_progress",
    label="Onboarding in progress",
    description="New joiners who have not finished their checklist.",
    resource=Resource.ONBOARDING,
)
def onboarding_in_progress(ctx, params) -> list[MetricPoint]:
    onboardings = ctx.scoped(
        _all("onboarding.EmployeeOnboarding"), Resource.ONBOARDING
    ).exclude(status="completed")

    return [MetricPoint(key="total", label="In progress", value=onboardings.count())]


# ===========================================================================
# Helpers
# ===========================================================================


def _all(label: str):
    from django.apps import apps as django_apps

    app_label, model_name = label.split(".")
    return django_apps.get_model(app_label, model_name).objects.all()


def _group_count(queryset, label_field: str, key_field: str) -> list[MetricPoint]:
    rows = (
        queryset.values(label_field, key_field)
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return [
        MetricPoint(
            key=str(row[key_field]),
            label=str(row[label_field] or "Unassigned"),
            value=row["count"],
        )
        for row in rows
    ]


def _payslips_in_period(ctx, params):
    """
    Payslips from runs that were actually approved.

    Draft runs are excluded on purpose: reporting a cost figure that includes
    a run finance has not signed off would present a proposal as a fact.
    """
    from apps.payroll.models import PayrollRunStatus

    return ctx.scoped(_all("payroll.Payslip"), Resource.PAYSLIP).filter(
        payroll_run__status__in=[PayrollRunStatus.APPROVED, PayrollRunStatus.PAID],
        created_at__date__gte=params["start"],
        created_at__date__lte=params["end"],
    )


def _month_ends(start: dt.date, end: dt.date) -> list[dt.date]:
    import calendar

    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(dt.date(year, month, calendar.monthrange(year, month)[1]))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out
