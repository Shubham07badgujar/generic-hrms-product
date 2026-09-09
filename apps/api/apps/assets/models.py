"""
Company assets and who holds them.

The design point worth stating: an allocation row is CLOSED, never reused. A
return sets `returned_at` on the existing row and the next allocation creates a
new one, so "who had this laptop in March?" is answerable forever. Overwriting
the employee on a single row would make the asset's history unrecoverable, and
that history is exactly what an exit checklist and an audit both need.

V1 allocation is MANUAL. Designation-based standard kits are deliberately not
built — the single `allocate()` entry point is the seam where they would attach
later, so adding them changes one caller rather than the model.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models import OrgOwnedModel


class AssetStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    ALLOCATED = "allocated", "Allocated"
    IN_MAINTENANCE = "in_maintenance", "In maintenance"
    RETIRED = "retired", "Retired"
    LOST = "lost", "Lost"


class AssetCondition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    DAMAGED = "damaged", "Damaged"
    UNUSABLE = "unusable", "Unusable"


class AllocationStatus(models.TextChoices):
    ACTIVE = "active", "Currently held"
    RETURNED = "returned", "Returned"
    OVERDUE = "overdue", "Overdue"
    WRITTEN_OFF = "written_off", "Written off"


class AssetCategory(OrgOwnedModel):
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=40, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    requires_serial = models.BooleanField(default=True)
    #: Blocks an exit until returned. A laptop does; a branded mug does not.
    is_returnable = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_assetcategory_org_code"
            ),
        ]
        ordering = ["name"]
        verbose_name_plural = "asset categories"

    def __str__(self) -> str:
        return self.name


class Asset(OrgOwnedModel):
    asset_tag = models.CharField(max_length=40, db_index=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    name = models.CharField(max_length=160)
    serial_number = models.CharField(max_length=120, blank=True)
    make = models.CharField(max_length=80, blank=True)
    model = models.CharField(max_length=80, blank=True)

    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    warranty_expires_on = models.DateField(null=True, blank=True)
    vendor = models.CharField(max_length=160, blank=True)

    location = models.ForeignKey(
        "organization.Location", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assets",
    )
    status = models.CharField(
        max_length=20, choices=AssetStatus.choices, default=AssetStatus.AVAILABLE, db_index=True
    )
    condition = models.CharField(
        max_length=20, choices=AssetCondition.choices, default=AssetCondition.GOOD
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "asset_tag"], name="uniq_asset_org_tag"
            ),
        ]
        ordering = ["asset_tag"]
        indexes = [models.Index(fields=["status", "category"])]

    def __str__(self) -> str:
        return f"{self.asset_tag} - {self.name}"

    def clean(self):
        super().clean()
        if self.category_id and self.category.requires_serial and not self.serial_number:
            raise ValidationError(
                {"serial_number": f"{self.category.name} assets must record a serial number."}
            )

    @property
    def current_allocation(self):
        return self.allocations.filter(status=AllocationStatus.ACTIVE).first()


class AssetAllocation(OrgOwnedModel):
    """
    One asset in one person's hands, for one period.

    The unique constraint is the real guarantee: an asset can be in exactly one
    pair of hands at a time. Two people holding the same laptop is a data error
    that a status field alone would happily allow.
    """

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="allocations")
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="asset_allocations"
    )

    allocated_at = models.DateTimeField()
    allocated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    condition_at_allocation = models.CharField(
        max_length=20, choices=AssetCondition.choices, default=AssetCondition.GOOD
    )
    allocation_notes = models.CharField(max_length=255, blank=True)
    expected_return_date = models.DateField(null=True, blank=True)

    returned_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    condition_at_return = models.CharField(
        max_length=20, choices=AssetCondition.choices, blank=True
    )
    return_notes = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20, choices=AllocationStatus.choices, default=AllocationStatus.ACTIVE,
        db_index=True,
    )
    #: Set when an unreturned asset is written off so an exit can complete.
    write_off_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-allocated_at"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["asset", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["asset"],
                condition=models.Q(status="active"),
                name="uniq_active_allocation_per_asset",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.asset.asset_tag} -> {self.employee.employee_code}"

    @property
    def is_open(self) -> bool:
        return self.status == AllocationStatus.ACTIVE


class AssetMaintenanceLog(OrgOwnedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="maintenance_logs")
    event = models.CharField(max_length=160)
    performed_at = models.DateTimeField()
    performed_by = models.CharField(max_length=160, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-performed_at"]
