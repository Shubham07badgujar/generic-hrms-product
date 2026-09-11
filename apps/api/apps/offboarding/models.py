"""
Exit: resignation, notice, clearance, settlement, approval.

THE SHAPE OF THE STATE MACHINE
------------------------------
The approved lifecycle names asset return, department clearance, HR clearance
and finance clearance as separate steps. They are modelled here as CATEGORIES
of one CLEARANCE stage rather than four sequential stages, because in practice
they run in parallel — finance does not wait for IT to revoke a login — and
serialising them would make the system misrepresent the work. What the spec
asks for is preserved exactly: each category is required, each is tracked
independently, and the approval gate refuses until every required one is done.

CLEARANCE IS CONFIGURATION, like onboarding and hiring before it. A template
owns items; starting an exit copies them onto the exit. Adding "return the
parking pass" is a row, not a branch.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import OrgOwnedModel
from core.validators import STORED_PATH_MAX, scoped_storage_path


class ExitType(models.TextChoices):
    RESIGNATION = "resignation", "Resignation"
    TERMINATION = "termination", "Termination"
    END_OF_CONTRACT = "end_of_contract", "End of contract"
    RETIREMENT = "retirement", "Retirement"
    ABANDONMENT = "abandonment", "Abandonment"


class ExitStage(models.TextChoices):
    """
    Where an exit has got to.

    Movement is owned by `services.offboarding`; there is no writable status
    field on the API, for the same reason the employment lifecycle has none.
    """

    INITIATED = "initiated", "Initiated"
    NOTICE_PERIOD = "notice_period", "Serving notice"
    CLEARANCE = "clearance", "Clearance in progress"
    PENDING_APPROVAL = "pending_approval", "Awaiting exit approval"
    APPROVED = "approved", "Approved, awaiting last working day"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


#: Stages after which nothing further happens.
CLOSED_STAGES = frozenset({ExitStage.COMPLETED, ExitStage.CANCELLED})


class ResignationStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn by the employee"


class ResignationReason(models.TextChoices):
    BETTER_OPPORTUNITY = "better_opportunity", "Better opportunity"
    COMPENSATION = "compensation", "Compensation"
    RELOCATION = "relocation", "Relocation"
    HIGHER_STUDIES = "higher_studies", "Higher studies"
    PERSONAL = "personal", "Personal reasons"
    HEALTH = "health", "Health"
    WORK_ENVIRONMENT = "work_environment", "Work environment"
    CAREER_CHANGE = "career_change", "Career change"
    OTHER = "other", "Other"


class ResignationRequest(OrgOwnedModel):
    """
    An employee's resignation, as a REQUEST.

    Deliberately not a status change. An employee submitting this does not
    become RESIGNED — HR reviews it, and only an approval moves their
    employment status, through the existing lifecycle service. That keeps the
    one place that owns status transitions unchanged and means a resignation
    always carries an approver.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="resignations"
    )
    resignation_date = models.DateField(
        default=timezone.localdate, help_text="The date the employee resigned."
    )
    requested_last_working_date = models.DateField()
    reason = models.CharField(
        max_length=30, choices=ResignationReason.choices, default=ResignationReason.OTHER
    )
    employee_comments = models.TextField(blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)
    submitted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    status = models.CharField(
        max_length=20,
        choices=ResignationStatus.choices,
        default=ResignationStatus.SUBMITTED,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    #: Required to reject. Accepting a resignation needs no defence; refusing
    #: one does, and the employee is entitled to know why.
    review_notes = models.TextField(blank=True)
    #: HR may agree a different date from the one requested.
    approved_last_working_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["status", "-submitted_at"])]
        constraints = [
            # One live request at a time. Without this an employee could submit
            # repeatedly and leave HR guessing which one counts.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(status="submitted"),
                name="uniq_open_resignation_per_employee",
            ),
        ]

    def __str__(self) -> str:
        return f"Resignation - {self.employee.employee_code} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == ResignationStatus.SUBMITTED


class ClearanceOwner(models.TextChoices):
    """
    Who owns a clearance item, as a ROLE-shaped bucket.

    Resolved against the actor when an item is completed, so a finance officer
    cannot tick off HR's exit interview even though both hold OFFBOARDING/EDIT.
    """

    HR = "hr", "HR"
    DEPARTMENT = "department", "Department head"
    MANAGER = "manager", "Reporting manager"
    IT = "it", "IT / administration"
    FINANCE = "finance", "Finance"
    EMPLOYEE = "employee", "The employee"


class ClearanceCategory(models.TextChoices):
    """
    The gates the approved lifecycle names.

    `is_required` on an item decides whether the gate binds; this says which
    gate the item belongs to, so the approval check can report "finance
    clearance outstanding" rather than a list of task titles.
    """

    HR = "hr", "HR clearance"
    DEPARTMENT = "department", "Department clearance"
    IT = "it", "IT and access"
    FINANCE = "finance", "Finance and settlement"
    ASSETS = "assets", "Company property"


class ClearanceTemplate(OrgOwnedModel):
    name = models.CharField(max_length=140)
    description = models.CharField(max_length=255, blank=True)
    department = models.ForeignKey(
        "organization.Department", on_delete=models.CASCADE, null=True, blank=True,
        related_name="clearance_templates",
    )
    exit_type = models.CharField(max_length=30, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ClearanceTemplateItem(OrgOwnedModel):
    template = models.ForeignKey(
        ClearanceTemplate, on_delete=models.CASCADE, related_name="items"
    )
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=20, choices=ClearanceCategory.choices)
    owner = models.CharField(max_length=20, choices=ClearanceOwner.choices)
    is_required = models.BooleanField(default=True)
    requires_evidence = models.BooleanField(
        default=False, help_text="Completion must attach a document."
    )
    #: Days relative to the last working day. Negative is before it.
    due_offset_days = models.SmallIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["template", "order", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "order"], name="uniq_clearance_item_order_per_template"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ClearanceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    WAIVED = "waived", "Waived"
    BLOCKED = "blocked", "Blocked"


def clearance_evidence_path(instance, filename: str) -> str:
    return scoped_storage_path(
        "exit-clearance",
        instance.exit_workflow.employee_id,
        filename,
        organization_id=instance.organization_id,
    )


class ExitWorkflow(OrgOwnedModel):
    """
    One employee's exit.

    Carries the notice period, the clearance state and the settlement, so the
    approval gate can answer "is this person free to leave?" from one row plus
    its items.
    """

    employee = models.OneToOneField(
        "employees.Employee", on_delete=models.CASCADE, related_name="exit_workflow"
    )
    exit_type = models.CharField(max_length=30, choices=ExitType.choices)
    stage = models.CharField(
        max_length=20, choices=ExitStage.choices, default=ExitStage.INITIATED, db_index=True
    )
    resignation = models.OneToOneField(
        ResignationRequest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="exit_workflow",
    )

    initiated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    initiated_at = models.DateTimeField(default=timezone.now)
    reason = models.TextField(blank=True)

    # --- notice period ---
    notice_start_date = models.DateField(null=True, blank=True)
    notice_days = models.PositiveSmallIntegerField(default=30)
    expected_last_working_date = models.DateField()
    #: Set when the exit completes, or when an early release is agreed.
    actual_last_working_date = models.DateField(null=True, blank=True)
    notice_waived = models.BooleanField(default=False)
    notice_waived_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notice_waived_at = models.DateTimeField(null=True, blank=True)
    notice_waiver_reason = models.TextField(blank=True)
    early_release_approved = models.BooleanField(default=False)
    early_release_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    early_release_reason = models.TextField(blank=True)

    # --- approval ---
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cancelled_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-initiated_at"]
        indexes = [models.Index(fields=["stage", "expected_last_working_date"])]

    def __str__(self) -> str:
        return f"Exit - {self.employee.employee_code}"

    def clean(self):
        super().clean()
        if (
            self.notice_start_date
            and self.expected_last_working_date
            and self.expected_last_working_date < self.notice_start_date
        ):
            raise ValidationError(
                {
                    "expected_last_working_date": (
                        "The last working day cannot fall before notice begins."
                    )
                }
            )

    # -- derived ----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.stage not in CLOSED_STAGES

    @property
    def required_items(self):
        return self.clearance_items.filter(is_required=True, is_active=True)

    @property
    def outstanding_items(self):
        return self.required_items.exclude(
            status__in=[ClearanceStatus.COMPLETED, ClearanceStatus.WAIVED]
        )

    @property
    def outstanding_categories(self) -> list[str]:
        """Which of the named gates are still open. Drives the blocker list."""
        return sorted(set(self.outstanding_items.values_list("category", flat=True)))

    @property
    def unreturned_assets(self):
        """
        Returnable company property still held.

        Non-returnable categories are excluded, so a branded notebook does not
        block someone leaving while a laptop does.
        """
        from apps.assets.models import AllocationStatus

        return self.employee.asset_allocations.filter(
            status=AllocationStatus.ACTIVE, asset__category__is_returnable=True
        ).select_related("asset", "asset__category")

    @property
    def progress(self) -> tuple[int, int]:
        items = self.clearance_items.filter(is_active=True)
        done = items.filter(
            status__in=[ClearanceStatus.COMPLETED, ClearanceStatus.WAIVED]
        ).count()
        return done, items.count()


class ExitClearanceItem(OrgOwnedModel):
    """
    One clearance line, copied from a template at exit time.

    Carries its own title/category/owner rather than pointing at the template
    item, so the record of what was asked survives the template being edited.
    """

    exit_workflow = models.ForeignKey(
        ExitWorkflow, on_delete=models.CASCADE, related_name="clearance_items"
    )
    source_item = models.ForeignKey(
        ClearanceTemplateItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    title = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    category = models.CharField(max_length=20, choices=ClearanceCategory.choices, db_index=True)
    owner = models.CharField(max_length=20, choices=ClearanceOwner.choices)
    assigned_to = models.ForeignKey(
        "employees.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="clearance_items_assigned",
    )

    is_required = models.BooleanField(default=True)
    requires_evidence = models.BooleanField(default=False)
    due_date = models.DateField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=ClearanceStatus.choices, default=ClearanceStatus.PENDING,
        db_index=True,
    )
    evidence = models.FileField(
        upload_to=clearance_evidence_path, max_length=STORED_PATH_MAX, null=True, blank=True
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["category", "order", "title"]
        indexes = [
            models.Index(fields=["exit_workflow", "status"]),
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_done(self) -> bool:
        return self.status in {ClearanceStatus.COMPLETED, ClearanceStatus.WAIVED}

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_date and not self.is_done and self.due_date < timezone.localdate())


class SettlementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In review"
    CLEARED = "cleared", "Cleared by finance"
    PAID = "paid", "Paid"
    DISPUTED = "disputed", "Disputed"


class FinalSettlement(OrgOwnedModel):
    """
    Full and final settlement — the FOUNDATION only.

    Deliberately arithmetic, not statutory. Gratuity, PF withdrawal, notice-pay
    tax treatment and the rest belong to the payroll/statutory engine, which is
    a separate phase with its own compliance oracle. Putting an approximation
    of them here would produce numbers that look authoritative and are not.

    What this does own is the ledger: what is owed, what is deducted, who
    cleared it, and when.
    """

    exit_workflow = models.OneToOneField(
        ExitWorkflow, on_delete=models.CASCADE, related_name="settlement"
    )
    final_working_date = models.DateField(null=True, blank=True)

    pending_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    leave_encashment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus_or_incentive = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    outstanding_advances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notice_shortfall_recovery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #: Filled from written-off allocations, so unrecovered property has a
    #: financial consequence rather than quietly disappearing.
    asset_recovery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=SettlementStatus.choices, default=SettlementStatus.DRAFT,
        db_index=True,
    )
    prepared_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cleared_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cleared_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Settlement - {self.exit_workflow.employee.employee_code}"

    @property
    def gross_earnings(self):
        return (
            self.pending_salary
            + self.leave_encashment
            + self.bonus_or_incentive
            + self.other_earnings
        )

    @property
    def total_deductions(self):
        return (
            self.outstanding_advances
            + self.notice_shortfall_recovery
            + self.asset_recovery
            + self.other_deductions
        )

    @property
    def net_payable(self):
        """May be negative: an employee can owe the company on the way out."""
        return self.gross_earnings - self.total_deductions

    @property
    def is_cleared(self) -> bool:
        return self.status in {SettlementStatus.CLEARED, SettlementStatus.PAID}


class RehireEligibility(models.TextChoices):
    ELIGIBLE = "eligible", "Eligible for rehire"
    ELIGIBLE_WITH_NOTES = "eligible_with_notes", "Eligible, with reservations"
    NOT_ELIGIBLE = "not_eligible", "Not eligible"
    UNDECIDED = "undecided", "Not decided"


class ExitInterview(OrgOwnedModel):
    """
    The exit conversation.

    Held under HR permissions specifically: candid feedback about a manager is
    exactly the thing that must not be visible to that manager, so this is
    gated on the OFFBOARDING resource and never surfaced through the
    department-scoped view of an employee.
    """

    exit_workflow = models.OneToOneField(
        ExitWorkflow, on_delete=models.CASCADE, related_name="interview"
    )
    conducted_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    conducted_at = models.DateTimeField(null=True, blank=True)

    primary_reason = models.CharField(
        max_length=30, choices=ResignationReason.choices, default=ResignationReason.OTHER
    )
    employee_feedback = models.TextField(blank=True)
    manager_feedback = models.TextField(blank=True)
    workplace_feedback = models.TextField(blank=True)
    improvement_suggestions = models.TextField(blank=True)

    would_recommend_employer = models.BooleanField(null=True, blank=True)
    rehire_eligibility = models.CharField(
        max_length=30, choices=RehireEligibility.choices, default=RehireEligibility.UNDECIDED
    )
    hr_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Exit interview - {self.exit_workflow.employee.employee_code}"

    @property
    def is_conducted(self) -> bool:
        return self.conducted_at is not None
