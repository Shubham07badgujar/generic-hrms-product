"""
Deliberate, bounded re-derivation of attendance days from stored punches.

Exists because policy improvements must NOT rewrite history on their own:
the sync only recomputes days that receive new punches, so days already
computed under an older policy keep their old verdicts until HR chooses to
re-run them — with this command.

Safety comes from `recompute_day` itself: manual and regularized rows are
untouched, days outside employment are ignored, and duplicates are
impossible. Dates are processed ascending because the monthly late tally
reads earlier days.

    manage.py recompute_attendance --from 2026-08-01 --to 2026-08-31
    manage.py recompute_attendance --from 2026-08-01 --to 2026-08-31 --employee EMP000101
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import CommandError

from core.management.orgcommand import OrganizationCommand

#: Mirrors the range-sync bound: big enough for a quarter, small enough that
#: a typo in the year cannot grind through decades.
MAX_DAYS = 92


class Command(OrganizationCommand):
    """
    One organization's attendance, under that organization's shift policy.

    Recomputation reads shift rules, holidays and employees, all of which are
    per customer, so "recompute the range" is only a meaningful instruction once
    somebody has said whose range. `--employee` is a code, and employee codes
    are unique per organization rather than globally -- so without a tenant this
    command could recompute a different company's employee of the same code.
    """

    help = (
        "Recompute device-sourced attendance for a date range under the "
        "current policy. Manual/regularized days are never touched. When "
        "recomputing mid-month, extend --to through month-end so later days "
        "see the corrected late tally."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--from", dest="date_from", required=True,
                            help="first date, YYYY-MM-DD")
        parser.add_argument("--to", dest="date_to", required=True,
                            help="last date, YYYY-MM-DD (inclusive)")
        parser.add_argument("--employee", dest="employee_code", default="",
                            help="limit to one employee code")

    def handle_for_organization(self, organization, *args, **options):
        from apps.attendance.models import EsslEmployeeLink
        from apps.attendance.services.calculation import recompute_day
        from apps.employees.models import Employee

        try:
            date_from = dt.date.fromisoformat(options["date_from"])
            date_to = dt.date.fromisoformat(options["date_to"])
        except ValueError as exc:
            raise CommandError(f"Dates are YYYY-MM-DD: {exc}") from exc
        if date_to < date_from:
            raise CommandError("--to is before --from.")
        if (date_to - date_from).days + 1 > MAX_DAYS:
            raise CommandError(f"Range exceeds {MAX_DAYS} days — split it up.")

        if options["employee_code"]:
            employees = list(
                Employee.objects.active().filter(
                    employee_code=options["employee_code"]
                )
            )
            if not employees:
                raise CommandError(
                    f"No active employee {options['employee_code']!r}."
                )
        else:
            # Only mapped employees can have device days worth re-deriving.
            employees = list(
                Employee.objects.active().filter(
                    pk__in=EsslEmployeeLink.objects.filter(
                        is_active=True
                    ).values_list("employee_id", flat=True)
                )
            )
            if not employees:
                self.stdout.write("No employees are mapped to eSSL IDs — nothing to do.")
                return

        written = skipped = 0
        days = [
            date_from + dt.timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)
        ]
        for employee in employees:
            for date in days:  # ascending: the tally reads earlier days
                if recompute_day(employee, date) is not None:
                    written += 1
                else:
                    skipped += 1

        self.stdout.write(
            f"{len(employees)} employee(s), {len(days)} day(s): "
            f"{written} recomputed, {skipped} protected/out-of-scope."
        )
