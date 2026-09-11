"""
The plans this deployment sells.

    manage.py seed_plans

Idempotent, and deliberately NOT destructive: it updates the plans it knows
about and leaves any the operator added by hand alone. A plan is referenced by
live subscriptions, so a seed that reconciled by deleting would take customers
with it.

The three below are a starting point, not a pricing decision. A deployment that
sells something else edits this list, or creates plans through the platform
console and stops running this command.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.platform.models import Plan, SupportLevel
from core.access.features import FeatureCode

#: (code, name, seats, disabled features, storage MB, support, order)
#:
#: Note what Starter disables and what it does not. Payroll and biometric
#: devices are the two with a real per-customer cost -- statutory rate sets to
#: keep current, and a device integration to support. Audit, employees and
#: leave are never disabled at any tier: a customer who cannot answer "who
#: changed this" has a compliance problem, and selling them the answer would be
#: selling them their own obligations back.
PLANS = [
    (
        "starter",
        "Starter",
        "Core HR, leave and attendance for a small team.",
        25,
        [
            FeatureCode.PAYROLL,
            FeatureCode.ATTENDANCE_BIOMETRIC,
            FeatureCode.ASSETS,
            FeatureCode.IT_ACCOUNTS,
            FeatureCode.REPORTING,
        ],
        2048,
        SupportLevel.COMMUNITY,
        10,
    ),
    (
        "growth",
        "Growth",
        "Everything in Starter, plus recruitment, payroll and reporting.",
        250,
        [FeatureCode.ATTENDANCE_BIOMETRIC],
        20480,
        SupportLevel.STANDARD,
        20,
    ),
    (
        "enterprise",
        "Enterprise",
        "Every module, no seat limit.",
        None,
        [],
        None,
        SupportLevel.PRIORITY,
        30,
    ),
]


class Command(BaseCommand):
    help = "Create or update the plans this deployment offers (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        for (
            code, name, description, seats, disabled, storage, support, order
        ) in PLANS:
            plan, created = Plan.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "employee_limit": seats,
                    "disabled_features": [str(f) for f in disabled],
                    "storage_limit_mb": storage,
                    "support_level": support,
                    "display_order": order,
                    "is_active": True,
                },
            )
            # `clean()` is what refuses an unknown feature or a disabled CORE,
            # and `update_or_create` does not call it. Running it here means a
            # typo in the list above fails the command rather than shipping a
            # plan that disables a module nobody can name.
            plan.full_clean(exclude=["created_by", "updated_by"])
            seats_label = "unlimited" if seats is None else f"{seats} seats"
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {'created' if created else 'updated'}  {plan.code:<12} "
                    f"{seats_label:<12} {len(plan.enabled_features)} features"
                )
            )
