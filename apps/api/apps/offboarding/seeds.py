"""
Default exit clearance configuration.

Configuration, not code — every item below can be deleted or replaced by an
organisation without touching the engine. It is seeded so a fresh install has a
working exit process, and it deliberately spans all five gates the approved
lifecycle names, each owned by a different bucket.
"""

from __future__ import annotations

from django.db import transaction

from .models import (
    ClearanceCategory as C,
    ClearanceOwner as O,
    ClearanceTemplate,
    ClearanceTemplateItem,
)

# (title, category, owner, required, requires_evidence, due offset, order)
#
# Offsets are relative to the LAST WORKING DAY: negative means it must be done
# before the person leaves, positive allows a short tail for finance.
DEFAULT_ITEMS = [
    # -- handover and the department --
    ("Complete knowledge handover to the team", C.DEPARTMENT, O.EMPLOYEE, True, False, -7, 10),
    ("Confirm handover is complete and adequate", C.DEPARTMENT, O.MANAGER, True, False, -3, 20),
    ("Department clearance sign-off", C.DEPARTMENT, O.DEPARTMENT, True, False, -1, 30),
    # -- company property --
    ("Return laptop and computing equipment", C.ASSETS, O.EMPLOYEE, True, False, -1, 40),
    ("Return access card and keys", C.ASSETS, O.EMPLOYEE, True, False, -1, 50),
    ("Verify all allocated assets are returned or written off", C.ASSETS, O.IT, True, False, 0, 60),
    # -- IT and access --
    ("Revoke system and application access", C.IT, O.IT, True, False, 0, 70),
    ("Deprovision the company email account", C.IT, O.IT, True, False, 0, 80),
    # -- HR --
    ("Conduct the exit interview", C.HR, O.HR, True, False, -2, 90),
    ("Collect and file exit documentation", C.HR, O.HR, True, True, 0, 100),
    ("Issue the relieving and experience letters", C.HR, O.HR, False, False, 7, 110),
    ("HR clearance sign-off", C.HR, O.HR, True, False, 0, 120),
    # -- finance --
    ("Recover outstanding advances and loans", C.FINANCE, O.FINANCE, True, False, 0, 130),
    ("Settle leave encashment and pending salary", C.FINANCE, O.FINANCE, True, False, 7, 140),
    ("Finance clearance sign-off", C.FINANCE, O.FINANCE, True, False, 7, 150),
]


@transaction.atomic
def seed_default_clearance_template() -> ClearanceTemplate:
    template, _ = ClearanceTemplate.objects.update_or_create(
        name="Standard exit clearance",
        defaults={
            "description": "Applies to any exit without a more specific template.",
            "is_default": True,
        },
    )

    for title, category, owner, required, evidence, offset, order in DEFAULT_ITEMS:
        ClearanceTemplateItem.objects.update_or_create(
            template=template,
            order=order,
            defaults={
                "title": title,
                "category": category,
                "owner": owner,
                "is_required": required,
                "requires_evidence": evidence,
                "due_offset_days": offset,
            },
        )
    return template


@transaction.atomic
def seed_all() -> dict[str, int]:
    """Idempotent. Safe on every deploy."""
    template = seed_default_clearance_template()
    return {"clearance_items": template.items.count()}
