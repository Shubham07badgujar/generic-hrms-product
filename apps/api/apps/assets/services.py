"""
Asset allocation and return.

`allocate()` is the SINGLE entry point, which is what makes designation-based
standard kits a later addition rather than a rewrite: a kit feature would build
a suggested list and call this once per item, changing one caller and no model.

Allocation rows are closed, never reused — see the model docstring.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee, EmployeeStatus
from core.access import Action, Resource, require

from .models import (
    AllocationStatus,
    Asset,
    AssetAllocation,
    AssetCondition,
    AssetStatus,
)


class AssetError(ValidationError):
    """A refused asset operation."""


@transaction.atomic
def allocate(
    *,
    asset: Asset,
    employee: Employee,
    actor,
    condition: str = AssetCondition.GOOD,
    notes: str = "",
    expected_return_date=None,
) -> AssetAllocation:
    """
    Hand an asset to an employee.

    The row is locked first: two allocations racing would otherwise both pass
    the availability check, and only the unique constraint would stop the
    second — as an IntegrityError rather than a readable message.
    """
    require(actor, Resource.ASSET_ALLOCATION, Action.CREATE)

    asset = Asset.objects.select_for_update().get(pk=asset.pk)

    open_allocation = asset.allocations.filter(status=AllocationStatus.ACTIVE).first()
    if open_allocation:
        raise AssetError(
            {
                "asset": (
                    f"{asset.asset_tag} is already allocated to "
                    f"{open_allocation.employee.full_name}. It must be returned first."
                )
            }
        )

    if asset.status in {AssetStatus.RETIRED, AssetStatus.LOST}:
        raise AssetError({"asset": f"{asset.asset_tag} is {asset.get_status_display().lower()}."})

    if employee.status == EmployeeStatus.EXITED:
        raise AssetError(
            {"employee": f"{employee.full_name} has exited and cannot be allocated company property."}
        )

    allocation = AssetAllocation.objects.create(
        asset=asset,
        employee=employee,
        allocated_at=timezone.now(),
        allocated_by=actor,
        condition_at_allocation=condition,
        allocation_notes=notes,
        expected_return_date=expected_return_date,
        status=AllocationStatus.ACTIVE,
    )

    asset.status = AssetStatus.ALLOCATED
    asset.save(update_fields=["status", "updated_at"])

    _audit(allocation, actor=actor, action="allocate")
    _satisfy_onboarding_item(allocation, actor=actor)
    return allocation


@transaction.atomic
def return_asset(
    *,
    allocation: AssetAllocation,
    actor,
    condition: str = AssetCondition.GOOD,
    notes: str = "",
    to_maintenance: bool = False,
) -> AssetAllocation:
    """
    Take an asset back.

    The condition it comes back in decides where the asset goes: damaged or
    unusable items do not silently return to the available pool for the next
    person to be handed.
    """
    require(actor, Resource.ASSET_ALLOCATION, Action.EDIT)

    if allocation.status != AllocationStatus.ACTIVE:
        raise AssetError(
            {"allocation": f"This allocation is already {allocation.get_status_display().lower()}."}
        )

    allocation.returned_at = timezone.now()
    allocation.received_by = actor
    allocation.condition_at_return = condition
    allocation.return_notes = notes
    allocation.status = AllocationStatus.RETURNED
    allocation.save()

    asset = allocation.asset
    asset.condition = condition
    if condition in {AssetCondition.DAMAGED, AssetCondition.UNUSABLE} or to_maintenance:
        asset.status = AssetStatus.IN_MAINTENANCE
    else:
        asset.status = AssetStatus.AVAILABLE
    asset.save(update_fields=["status", "condition", "updated_at"])

    _audit(allocation, actor=actor, action="return")
    return allocation


@transaction.atomic
def write_off(*, allocation: AssetAllocation, actor, reason: str) -> AssetAllocation:
    """
    Close an allocation for an asset that is not coming back.

    The sanctioned way past the exit gate. It demands DELETE on the allocation
    resource rather than EDIT, because accepting the loss of company property
    is a heavier call than recording its return, and it is always explained.
    """
    require(actor, Resource.ASSET_ALLOCATION, Action.DELETE)

    if not reason.strip():
        raise AssetError({"reason": "Writing off company property requires a reason."})
    if allocation.status != AllocationStatus.ACTIVE:
        raise AssetError({"allocation": "Only an open allocation can be written off."})

    allocation.status = AllocationStatus.WRITTEN_OFF
    allocation.write_off_reason = reason.strip()
    allocation.returned_at = timezone.now()
    allocation.received_by = actor
    allocation.save()

    asset = allocation.asset
    asset.status = AssetStatus.LOST
    asset.save(update_fields=["status", "updated_at"])

    _audit(allocation, actor=actor, action="write_off", extra={"reason": reason.strip()})
    return allocation


def outstanding_for(employee: Employee):
    """Returnable property still held. Used by the exit gate and the profile."""
    return (
        employee.asset_allocations.filter(
            status=AllocationStatus.ACTIVE, asset__category__is_returnable=True
        )
        .select_related("asset", "asset__category")
        .order_by("-allocated_at")
    )


def _satisfy_onboarding_item(allocation: AssetAllocation, *, actor) -> None:
    """Tick the 'device allocation' line, if the checklist has one waiting."""
    from apps.onboarding.models import ItemKind, ItemStatus, OnboardingItem
    from apps.onboarding.services import _maybe_close

    item = (
        OnboardingItem.objects.filter(
            onboarding__employee=allocation.employee,
            kind=ItemKind.ASSET,
            status__in=[ItemStatus.PENDING, ItemStatus.IN_PROGRESS, ItemStatus.BLOCKED],
        )
        .select_related("onboarding")
        .order_by("order")
        .first()
    )
    if item is None:
        return
    item.status = ItemStatus.COMPLETED
    item.completed_at = timezone.now()
    item.completed_by = actor
    item.notes = f"{allocation.asset.asset_tag} allocated"
    item.save(update_fields=["status", "completed_at", "completed_by", "notes", "updated_at"])
    _maybe_close(item.onboarding, actor=actor)


def _audit(allocation: AssetAllocation, *, actor, action: str, extra: dict | None = None) -> None:
    """
    ALLOCATE and RETURN get their own audit verbs.

    "Updated an allocation" would be technically true and useless; the audit
    log is read by someone asking who was given what, and when it came back.
    """
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    verbs = {
        "allocate": AuditAction.ALLOCATE,
        "return": AuditAction.RETURN,
        "write_off": AuditAction.UPDATE,
    }

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=verbs[action],
        resource=Resource.ASSET_ALLOCATION,
        entity_type="assets.AssetAllocation",
        entity_id=str(allocation.pk),
        entity_label=str(allocation),
        after={
            "event": f"asset_{action}",
            "asset_tag": allocation.asset.asset_tag,
            "asset": allocation.asset.name,
            "employee": allocation.employee.employee_code,
            "condition": (
                allocation.condition_at_return
                if action != "allocate"
                else allocation.condition_at_allocation
            ),
            **(extra or {}),
        },
        request_id=get_request_id(),
    )
