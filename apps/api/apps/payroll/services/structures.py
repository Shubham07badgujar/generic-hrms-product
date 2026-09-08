"""
Salary components and structures.

A structure is never edited once payroll has run against it. A revision closes
the current row and opens a new one, so a payslip issued last March can still
be explained by the structure that was in force then. Editing in place would
make historical payslips unreproducible, which is the point at which a payroll
system stops being able to answer "why was I paid this?".
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.statutory.rules.money import ZERO, money

from ..models import (
    CalcType,
    ComponentType,
    Rounding,
    SalaryComponent,
    SalaryStructure,
    SalaryStructureLine,
)
from .audit import audit_event


def monthly_amount_for(
    component: SalaryComponent, value: Decimal, base_amounts: dict[str, Decimal]
) -> Decimal:
    """
    Resolve a configured line into the monthly figure payroll will actually use.

    PERCENT_OF components resolve against amounts already computed in this same
    structure, so HRA can be 40% of Basic without either being restated when the
    other changes.
    """
    if component.calc_type == CalcType.PERCENT_OF:
        base = base_amounts.get(component.percent_of_code, ZERO)
        amount = base * value / Decimal("100")
    else:
        amount = value
    return _round(amount, component.rounding)


def _round(amount: Decimal, mode: str) -> Decimal:
    if mode == Rounding.NEAREST:
        return money(amount.quantize(Decimal("1")))
    if mode == Rounding.UP:
        return money(amount.to_integral_value(rounding="ROUND_CEILING"))
    if mode == Rounding.DOWN:
        return money(amount.to_integral_value(rounding="ROUND_FLOOR"))
    return money(amount)


@transaction.atomic
def create_salary_structure(
    *,
    actor,
    employee,
    ctc_annual: Decimal,
    valid_from: dt.date,
    lines: list[dict],
    revision_reason: str = "",
    pf_applicable: bool = True,
    esi_applicable: bool = True,
    pt_applicable: bool = True,
    tds_applicable: bool = True,
    gratuity_applicable: bool = True,
) -> SalaryStructure:
    """
    Open a new structure, closing whatever is currently in force.

    Atomic because a half-applied revision leaves an employee either with two
    open structures (so "current salary" is ambiguous) or none (so payroll skips
    them entirely). Both are worse than the revision failing outright.
    """
    if not lines:
        raise ValidationError("A salary structure needs at least one component line.")

    previous = (
        SalaryStructure.objects.select_for_update()
        .filter(employee=employee, valid_to__isnull=True, is_active=True)
        .first()
    )
    earliest = (
        SalaryStructure.objects.select_for_update()
        .filter(employee=employee, is_active=True)
        .order_by("valid_from")
        .first()
    )

    #: Set only on the BACK-FILL path: a structure for months before this
    #: employee's pay history began is born closed, ending the day before the
    #: earliest existing structure starts, so the timeline stays continuous
    #: and nothing already in force moves.
    backfill_valid_to: dt.date | None = None

    if previous is None and earliest is None:
        pass  # first structure ever — open-ended
    elif valid_from < earliest.valid_from:
        # Back-fill: pay an earlier month that predates the whole history.
        # Locked payslips are untouchable regardless (they keep their own
        # structure FK), so this can only make previously-skipped months
        # payable — never restate a month that was already paid.
        backfill_valid_to = earliest.valid_from - dt.timedelta(days=1)
    elif previous and valid_from > previous.valid_from:
        # Ordinary revision — the current structure closes the day before.
        previous.valid_to = valid_from - dt.timedelta(days=1)
        previous.updated_by = actor
        previous.save(update_fields=["valid_to", "updated_by", "updated_at"])
    else:
        raise ValidationError(
            f"{valid_from} is already covered by an existing structure. "
            f"Start after {previous.valid_from if previous else earliest.valid_from} "
            f"to revise forward, or before {earliest.valid_from} to back-fill "
            f"earlier months. Months already covered are history and are never "
            f"rewritten."
        )

    structure = SalaryStructure.objects.create(
        employee=employee,
        ctc_annual=ctc_annual,
        valid_from=valid_from,
        valid_to=backfill_valid_to,
        revision_reason=revision_reason,
        pf_applicable=pf_applicable,
        esi_applicable=esi_applicable,
        pt_applicable=pt_applicable,
        tds_applicable=tds_applicable,
        gratuity_applicable=gratuity_applicable,
        approved_by=actor,
        created_by=actor,
        updated_by=actor,
    )

    # Two passes: fixed amounts first, so percentage components have something
    # to be a percentage OF. A single pass would silently resolve HRA to zero
    # whenever it happened to be processed before Basic.
    components = {
        str(c.pk): c
        for c in SalaryComponent.objects.filter(
            pk__in=[line["component"] for line in lines]
        )
    }
    base_amounts: dict[str, Decimal] = {}
    resolved: list[tuple[SalaryComponent, Decimal, Decimal]] = []

    for pass_type in (CalcType.FIXED, CalcType.PERCENT_OF):
        for line in lines:
            component = components.get(str(line["component"]))
            if component is None or component.calc_type != pass_type:
                continue
            value = Decimal(str(line.get("value", "0")))
            amount = monthly_amount_for(component, value, base_amounts)
            base_amounts[component.code] = amount
            resolved.append((component, value, amount))

    SalaryStructureLine.objects.bulk_create([
        SalaryStructureLine(
            salary_structure=structure,
            component=component,
            value=value,
            monthly_amount=amount,
            created_by=actor,
            updated_by=actor,
        )
        for component, value, amount in resolved
    ])

    audit_event(
        structure,
        actor=actor,
        entity_type="SalaryStructure",
        verb="create",
        after={
            "employee": employee.employee_code,
            "ctc_annual": str(ctc_annual),
            "valid_from": valid_from.isoformat(),
            "valid_to": backfill_valid_to.isoformat() if backfill_valid_to else None,
            "lines": len(resolved),
            "supersedes": str(previous.pk) if previous and not backfill_valid_to else None,
            "backfill": backfill_valid_to is not None,
        },
    )
    return structure


def structure_in_force(employee, on_date: dt.date) -> SalaryStructure | None:
    """The structure covering `on_date` — not simply the latest one."""
    return (
        SalaryStructure.objects.filter(employee=employee, is_active=True, valid_from__lte=on_date)
        .filter(models_q_open_or_covering(on_date))
        .order_by("-valid_from")
        .first()
    )


def models_q_open_or_covering(on_date: dt.date):
    from django.db.models import Q

    return Q(valid_to__isnull=True) | Q(valid_to__gte=on_date)


@transaction.atomic
def save_component(*, actor, data: dict, component: SalaryComponent | None = None) -> SalaryComponent:
    """Create or update a component in the catalogue."""
    if component is None:
        component = SalaryComponent(created_by=actor)
        verb = "create"
    else:
        verb = "update"

    for field in (
        "code", "name", "component_type", "calc_type", "percent_of_code",
        "is_taxable", "is_part_of_ctc", "is_wage", "rounding", "display_order",
    ):
        if field in data:
            setattr(component, field, data[field])

    component.updated_by = actor
    component.full_clean()
    component.save()

    audit_event(
        component,
        actor=actor,
        entity_type="SalaryComponent",
        verb=verb,
        after={"code": component.code, "type": component.component_type,
               "is_wage": component.is_wage},
    )
    return component


#: The minimum catalogue a payroll run needs. Seeded rather than hardcoded so
#: an organisation can rename or extend them without a release.
DEFAULT_COMPONENTS = [
    {"code": "BASIC", "name": "Basic", "component_type": ComponentType.EARNING,
     "is_wage": True, "display_order": 10},
    {"code": "DA", "name": "Dearness Allowance", "component_type": ComponentType.EARNING,
     "is_wage": True, "display_order": 20},
    {"code": "HRA", "name": "House Rent Allowance", "component_type": ComponentType.EARNING,
     "calc_type": CalcType.PERCENT_OF, "percent_of_code": "BASIC", "display_order": 30},
    {"code": "CONVEYANCE", "name": "Conveyance Allowance",
     "component_type": ComponentType.EARNING, "display_order": 40},
    {"code": "SPECIAL", "name": "Special Allowance",
     "component_type": ComponentType.EARNING, "display_order": 50},
    {"code": "MEDICAL", "name": "Medical Allowance",
     "component_type": ComponentType.EARNING, "display_order": 60},
]
