"""
Onboarding checklists and employee letters.

ONBOARDING IS CONFIGURATION, NOT CODE — the same principle the hiring workflow
follows. A template owns ordered items; hiring someone copies the template into
a per-employee checklist. "Verify documents", "Company email", "Department
introduction" are rows, so a new joiner type means a new template, not a new
branch. Nothing here reads a designation or a job title.

The template is COPIED rather than referenced, deliberately. An employee's
checklist must record what was asked of THEM; editing the template a year later
to add a step must not retroactively make a completed onboarding incomplete.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import BaseModel
from core.validators import STORED_PATH_MAX, scoped_storage_path


class ItemKind(models.TextChoices):
    DOCUMENT = "document", "Document to collect"
    TASK = "task", "Task to complete"
    ACCOUNT = "account", "Account or access to provision"
    ASSET = "asset", "Asset to allocate"
    ACKNOWLEDGEMENT = "acknowledgement", "Policy to acknowledge"


class ItemOwner(models.TextChoices):
    """
    Who is responsible, expressed as a ROLE-shaped bucket rather than a person.

    Resolved to an actual assignee when the checklist is created, because the
    HR Manager who owns "verify documents" differs per hire while the
    responsibility does not.
    """

    HR = "hr", "HR"
    MANAGER = "manager", "Reporting manager"
    EMPLOYEE = "employee", "The employee"
    DEPARTMENT_HEAD = "department_head", "Department head"
    ADMIN = "admin", "Administrator"


class OnboardingTemplate(BaseModel):
    name = models.CharField(max_length=140)
    description = models.CharField(max_length=255, blank=True)
    #: Optional narrowing. A template with neither is the organisation default.
    department = models.ForeignKey(
        "organization.Department", on_delete=models.CASCADE, null=True, blank=True,
        related_name="onboarding_templates",
    )
    employment_type = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OnboardingTemplateItem(BaseModel):
    template = models.ForeignKey(
        OnboardingTemplate, on_delete=models.CASCADE, related_name="items"
    )
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    kind = models.CharField(max_length=20, choices=ItemKind.choices, default=ItemKind.TASK)
    owner = models.CharField(max_length=20, choices=ItemOwner.choices, default=ItemOwner.HR)
    document_type = models.ForeignKey(
        "employees.DocumentType", on_delete=models.PROTECT, null=True, blank=True,
        related_name="onboarding_items",
    )
    is_mandatory = models.BooleanField(default=True)
    #: Days from the joining date. Negative means before the person starts,
    #: which is how pre-joining paperwork is expressed.
    due_offset_days = models.SmallIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["template", "order", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "order"], name="uniq_onboarding_item_order_per_template"
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        super().clean()
        if self.kind == ItemKind.DOCUMENT and not self.document_type_id:
            raise ValidationError(
                {"document_type": "A document item must name the document type to collect."}
            )


class OnboardingStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EmployeeOnboarding(BaseModel):
    employee = models.OneToOneField(
        "employees.Employee", on_delete=models.CASCADE, related_name="onboarding"
    )
    #: SET_NULL: deleting a retired template must not delete the record of what
    #: people were actually asked to do.
    template = models.ForeignKey(
        OnboardingTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="onboardings",
    )
    template_name = models.CharField(max_length=140, blank=True)
    joining_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=OnboardingStatus.choices, default=OnboardingStatus.IN_PROGRESS,
        db_index=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "joining_date"])]

    def __str__(self) -> str:
        return f"Onboarding - {self.employee.employee_code}"

    # -- derived ----------------------------------------------------------

    @property
    def mandatory_items(self):
        return self.items.filter(is_mandatory=True, is_active=True)

    @property
    def outstanding_mandatory(self):
        return self.mandatory_items.exclude(
            status__in=[ItemStatus.COMPLETED, ItemStatus.WAIVED]
        )

    @property
    def is_complete(self) -> bool:
        """Complete when nothing MANDATORY is outstanding. Optional items may linger."""
        return not self.outstanding_mandatory.exists()

    @property
    def progress(self) -> tuple[int, int]:
        items = self.items.filter(is_active=True)
        done = items.filter(status__in=[ItemStatus.COMPLETED, ItemStatus.WAIVED]).count()
        return done, items.count()


class ItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    SUBMITTED = "submitted", "Submitted, awaiting verification"
    COMPLETED = "completed", "Completed"
    WAIVED = "waived", "Waived"
    BLOCKED = "blocked", "Blocked"


def onboarding_item_path(instance, filename: str) -> str:
    return scoped_storage_path("onboarding", instance.onboarding.employee_id, filename)


class OnboardingItem(BaseModel):
    """
    One line of one employee's checklist.

    Carries its own copy of title/kind/owner rather than pointing at the
    template item, so the record of what was asked survives the template being
    edited or retired.
    """

    onboarding = models.ForeignKey(
        EmployeeOnboarding, on_delete=models.CASCADE, related_name="items"
    )
    source_item = models.ForeignKey(
        OnboardingTemplateItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    title = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    kind = models.CharField(max_length=20, choices=ItemKind.choices, default=ItemKind.TASK)
    owner = models.CharField(max_length=20, choices=ItemOwner.choices, default=ItemOwner.HR)
    #: The person actually responsible, resolved from `owner` at creation.
    assigned_to = models.ForeignKey(
        "employees.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="onboarding_items_assigned",
    )
    document_type = models.ForeignKey(
        "employees.DocumentType", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    is_mandatory = models.BooleanField(default=True)
    due_date = models.DateField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=ItemStatus.choices, default=ItemStatus.PENDING, db_index=True
    )
    file = models.FileField(
        upload_to=onboarding_item_path, max_length=STORED_PATH_MAX, null=True, blank=True
    )
    #: Set when a DOCUMENT item is satisfied by an uploaded employee document,
    #: so the checklist and the document library never disagree.
    document = models.ForeignKey(
        "employees.EmployeeDocument", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="onboarding_items",
    )

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "title"]
        indexes = [
            models.Index(fields=["onboarding", "status"]),
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_done(self) -> bool:
        return self.status in {ItemStatus.COMPLETED, ItemStatus.WAIVED}

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_date and not self.is_done and self.due_date < timezone.localdate())


# ===========================================================================
# Letters
# ===========================================================================


class LetterType(models.TextChoices):
    OFFER = "offer", "Offer letter"
    APPOINTMENT = "appointment", "Appointment letter"
    JOINING = "joining", "Joining letter"
    CONFIRMATION = "confirmation", "Confirmation letter"
    EXTENSION = "extension", "Probation extension letter"
    EXPERIENCE = "experience", "Experience letter"
    RELIEVING = "relieving", "Relieving letter"
    WARNING = "warning", "Warning letter"
    OTHER = "other", "Other"


class LetterTemplate(BaseModel):
    """
    A letter as a template, not a hand-written document.

    `body_html` is rendered with the Django template engine against a context
    the letter service assembles, so wording changes are configuration. Version
    is stamped onto every generated letter: a letter issued last March must
    remain explainable even after the template is rewritten.
    """

    name = models.CharField(max_length=140)
    letter_type = models.CharField(max_length=30, choices=LetterType.choices, db_index=True)
    subject = models.CharField(max_length=200)
    body_html = models.TextField()
    version = models.PositiveSmallIntegerField(default=1)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["letter_type", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["letter_type"],
                condition=models.Q(is_default=True, is_active=True),
                name="uniq_default_letter_template_per_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class LetterStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    REVOKED = "revoked", "Revoked"


def letter_path(instance, filename: str) -> str:
    return scoped_storage_path("letters", instance.employee_id, filename)


class EmployeeLetter(BaseModel):
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="letters"
    )
    letter_type = models.CharField(max_length=30, choices=LetterType.choices, db_index=True)
    template = models.ForeignKey(
        LetterTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="letters"
    )
    #: Frozen at generation. The template may change; this letter may not.
    template_name = models.CharField(max_length=140, blank=True)
    template_version = models.PositiveSmallIntegerField(default=1)

    subject = models.CharField(max_length=200)
    body_html = models.TextField(blank=True)
    merge_context = models.JSONField(default=dict, blank=True)
    pdf_file = models.FileField(
        upload_to=letter_path, max_length=STORED_PATH_MAX, null=True, blank=True
    )

    status = models.CharField(
        max_length=20, choices=LetterStatus.choices, default=LetterStatus.DRAFT, db_index=True
    )
    generated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    generated_at = models.DateTimeField(default=timezone.now)
    issued_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-generated_at"]
        indexes = [models.Index(fields=["employee", "letter_type"])]

    def __str__(self) -> str:
        return f"{self.get_letter_type_display()} - {self.employee.employee_code}"
