"""
Default leave configuration — data the organisation edits, seeded so a fresh
install has a working set. Idempotent.

A STARTER LEAVE POLICY, as data (edit or replace per company):
  * One combined Paid Leave (PL/CL) type: 19 days/year for the six-day week,
    ACCRUING at 19/12 ≈ 1.58 days/month — no advance leave, ever.
  * Sick/medical leave keeps its own type and REQUIRES a medical certificate.
  * LWP stays for unpaid leave.
  * The old separate casual/earned types are retired (deactivated) — their
    history and any open requests remain decidable; new requests use PL/CL.
  * Public holidays live on the calendar, never deducted from balances.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db import transaction

from core.models import org_scoped

from .models import Holiday, HolidayCalendar, LeavePolicy, LeaveSettings, LeaveType

# (code, name, is_paid, order)
LEAVE_TYPES = [
    ("paid", "Paid Leave (PL/CL)", True, 10),
    ("sick", "Sick Leave", True, 20),
    ("lwp", "Leave Without Pay", False, 40),
]

#: Types the current policy no longer offers. Deactivated, never deleted:
#: balances, ledgers and past requests stay intact and decidable.
RETIRED_TYPES = ["casual", "earned"]

# code -> policy defaults
POLICIES = {
    "paid": dict(
        name="Paid Leave (PL/CL) — standard",
        annual_allocation=Decimal("19"),
        accrual_per_month=Decimal("1.58"),  # 19 ÷ 12; December completes the 19
        allow_half_day=True,
        max_consecutive_days=6,
        carry_forward=True, carry_forward_limit=Decimal("19"),
        is_encashable=True,
    ),
    "sick": dict(
        name="Sick Leave — standard", annual_allocation=Decimal("8"),
        allow_half_day=True, min_notice_days=0,
        requires_attachment=True,  # a valid medical certificate
        carry_forward=False,
    ),
    "lwp": dict(
        name="Leave Without Pay — standard", annual_allocation=Decimal("0"),
        allow_half_day=True, allow_negative_balance=True,
    ),
}

#: Declared public holidays. Never deducted from any balance — `working_days`
#: skips them. Editable in the UI; this list only ADDS missing rows.
PUBLIC_HOLIDAYS_2026 = [
    (dt.date(2026, 3, 4), "Holi"),
    (dt.date(2026, 9, 14), "Ganpati"),
    (dt.date(2026, 9, 25), "Ganpati"),
    (dt.date(2026, 11, 8), "Diwali"),
    (dt.date(2026, 11, 11), "Diwali"),
]


@transaction.atomic
def seed_leave() -> dict:
    types = {}
    for code, name, is_paid, order in LEAVE_TYPES:
        row, _ = org_scoped(LeaveType).update_or_create(
            code=code,
            defaults={"name": name, "is_paid": is_paid, "order": order, "is_active": True},
        )
        types[code] = row

    # Scoped: an unscoped bulk update here DEACTIVATED every other
    # organization's leave types and policies of the same code.
    org_scoped(LeaveType).filter(code__in=RETIRED_TYPES).update(is_active=False)
    org_scoped(LeavePolicy).filter(
        leave_type__code__in=RETIRED_TYPES
    ).update(is_active=False)

    for code, defaults in POLICIES.items():
        org_scoped(LeavePolicy).update_or_create(
            leave_type=types[code], department=None, employment_type="",
            defaults={**defaults, "is_active": True},
        )

    # The org default calendar: Sunday off (six-day week). Editable, never
    # assumed in code.
    calendar, _ = org_scoped(HolidayCalendar).get_or_create(
        location=None, defaults={"name": "Standard calendar", "weekly_off": [6]}
    )
    for date, name in PUBLIC_HOLIDAYS_2026:
        org_scoped(Holiday).get_or_create(
            calendar=calendar, date=date, defaults={"name": name}
        )

    LeaveSettings.get_solo()

    return {"types": len(types), "policies": len(POLICIES), "calendar": calendar.name}
