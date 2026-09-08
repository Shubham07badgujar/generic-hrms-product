"""
Investment declarations — the employee's tax election and Chapter VI-A figures.

Two rules that look like restrictions and are actually protections.

An employee may only change a declaration while it is DRAFT or REJECTED. Once
submitted it is evidence Finance is working from, and a figure that can change
underneath the person verifying it is not evidence.

A declaration is stored EXACTLY as declared. The caps are applied during
computation, never on the way in, so the employee's own record always shows
what they claimed rather than what the system allowed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.access.catalog import Resource

from ..models import DeclarationStatus, InvestmentDeclaration
from .audit import audit_event

#: Sections an employee may declare. An unknown key is rejected rather than
#: stored: a typo'd "80CC" would otherwise sit in the record looking valid and
#: silently contribute nothing, which the employee only discovers at filing.
KNOWN_SECTIONS = frozenset({
    "80C", "80D", "80CCD_1B", "80TTA", "80E", "80G", "24b", "HRA_exempt",
})


@transaction.atomic
def save_declaration(
    *, actor, employee, financial_year: str, regime: str = "", declarations: dict | None = None
) -> InvestmentDeclaration:
    declaration, created = InvestmentDeclaration.objects.get_or_create(
        employee=employee,
        financial_year=financial_year,
        defaults={"created_by": actor, "updated_by": actor},
    )

    if declaration.status in {DeclarationStatus.SUBMITTED, DeclarationStatus.VERIFIED}:
        raise ValidationError(
            f"This declaration has been {declaration.get_status_display().lower()} and "
            f"can no longer be edited. Ask Finance to reopen it if it is wrong."
        )

    cleaned: dict[str, str] = {}
    for section, amount in (declarations or {}).items():
        if section not in KNOWN_SECTIONS:
            raise ValidationError(
                f"'{section}' is not a section this system recognises. "
                f"Valid sections: {', '.join(sorted(KNOWN_SECTIONS))}."
            )
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(f"'{section}' must be an amount, got {amount!r}.")
        if value < 0:
            raise ValidationError(f"'{section}' cannot be negative.")
        cleaned[section] = str(value.quantize(Decimal("0.01")))

    declaration.regime = regime or ""
    declaration.declarations = cleaned
    declaration.status = DeclarationStatus.SUBMITTED
    declaration.updated_by = actor
    declaration.save(update_fields=["regime", "declarations", "status", "updated_by", "updated_at"])

    audit_event(
        declaration, actor=actor, entity_type="InvestmentDeclaration",
        verb="create" if created else "update",
        # The figures are deliberately absent: the trail records that a
        # declaration was made, not the employee's private financial affairs.
        after={"financial_year": financial_year, "regime": regime or "(none — default applies)",
               "sections_declared": sorted(cleaned), "status": declaration.status},
        resource=Resource.SALARY,
    )
    return declaration


@transaction.atomic
def review_declaration(
    declaration: InvestmentDeclaration, *, actor, approve: bool, note: str = ""
) -> InvestmentDeclaration:
    """Finance verifies the proofs behind a declaration, or sends it back."""
    if declaration.status != DeclarationStatus.SUBMITTED:
        raise ValidationError("Only a submitted declaration can be reviewed.")
    if not approve and len(note.strip()) < 10:
        raise ValidationError("Say what is wrong with the declaration so it can be fixed.")

    declaration.status = (
        DeclarationStatus.VERIFIED if approve else DeclarationStatus.REJECTED
    )
    declaration.verified_by = actor
    declaration.verified_at = timezone.now()
    declaration.review_note = note
    declaration.save(update_fields=[
        "status", "verified_by", "verified_at", "review_note", "updated_at"
    ])

    audit_event(
        declaration, actor=actor, entity_type="InvestmentDeclaration",
        verb="approve" if approve else "reject",
        after={"status": declaration.status, "note": note},
        resource=Resource.SALARY,
    )
    return declaration
