"""
Organizational structure: departments, designations, locations, levels, teams.

Single organization — there is no tenant column anywhere. `OrgSettings` is a
singleton holding the company's own particulars.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.access.catalog import DepartmentKind, Layer
from core.models import BaseModel
from core.validators import STORED_PATH_MAX


class OrgSettings(BaseModel):
    """The organization itself. Exactly one row."""

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=250, blank=True)

    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    tan = models.CharField(max_length=10, blank=True)
    cin = models.CharField(max_length=21, blank=True)
    epf_number = models.CharField(max_length=30, blank=True)
    esi_number = models.CharField(max_length=30, blank=True)

    financial_year_start_month = models.PositiveSmallIntegerField(default=4)
    currency = models.CharField(max_length=3, default="INR")
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")

    employee_code_prefix = models.CharField(max_length=8, default="EMP")
    employee_code_next = models.PositiveIntegerField(default=1)

    logo = models.ImageField(
        upload_to="org/", max_length=STORED_PATH_MAX, null=True, blank=True
    )
    signatory_name = models.CharField(max_length=150, blank=True)
    signatory_designation = models.CharField(max_length=150, blank=True)
    #: The signatory's scanned signature, stamped onto generated letters
    #: (offer letters today). Never served publicly — it reaches candidates
    #: only inside a PDF the office chose to send.
    signature = models.ImageField(
        upload_to="org/", max_length=STORED_PATH_MAX, null=True, blank=True
    )

    class Meta:
        verbose_name = "organization settings"
        verbose_name_plural = "organization settings"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get(cls) -> "OrgSettings":
        """The singleton, created on first access."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(name="Organization")
        return obj

    def save(self, *args, **kwargs):
        if not self.pk and OrgSettings.objects.exists():
            raise ValidationError(
                "Organization settings already exist. This system serves a single "
                "organization; edit the existing row rather than adding another."
            )
        super().save(*args, **kwargs)


class Location(BaseModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    #: Drives Professional Tax jurisdiction and the holiday calendar.
    state = models.CharField(max_length=8, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    is_head_office = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Department(BaseModel):
    """
    A department. `kind` groups it into one of the four functional areas the
    role hierarchy is built around.

    IMPORTANT: `kind` drives dashboards, BI grouping and role-compatibility
    validation. It does NOT drive authorization scope — that comes from
    `Employee.department`, so moving a person between departments changes what
    they can see without anyone touching their role.
    """

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    kind = models.CharField(
        max_length=20, choices=DepartmentKind.choices, default=DepartmentKind.OTHER, db_index=True
    )
    description = models.CharField(max_length=255, blank=True)

    parent_department = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    head_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="heading_departments",
    )

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["kind", "is_active"])]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        self._assert_no_parent_cycle()

    def _assert_no_parent_cycle(self) -> None:
        """
        A department cannot be its own ancestor.

        Without this the department-closure walk in the access engine would
        need to defend itself against malformed data on every request. Better
        to make the data impossible.
        """
        seen = {self.pk} if self.pk else set()
        node = self.parent_department
        while node is not None:
            if node.pk in seen:
                raise ValidationError(
                    {"parent_department": "This would create a cycle in the department tree."}
                )
            seen.add(node.pk)
            node = node.parent_department

    def descendant_ids(self) -> set:
        """This department plus everything beneath it."""
        found = {self.pk}
        frontier = [self.pk]
        while frontier:
            children = list(
                Department.objects.filter(
                    parent_department_id__in=frontier, is_active=True
                )
                .exclude(pk__in=found)
                .values_list("pk", flat=True)
            )
            if not children:
                break
            found.update(children)
            frontier = children
        return found


class Designation(BaseModel):
    """A job title. Distinct from Role: a title describes the work, a role
    grants authority. 'Senior Physiotherapist' is a designation; `therapist`
    is the role."""

    title = models.CharField(max_length=150)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="designations"
    )
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["title", "department"], name="uniq_designation_title_department"
            )
        ]

    def __str__(self) -> str:
        return self.title


class EmployeeLevel(BaseModel):
    """
    Seniority band, aligned to the five-layer hierarchy.

    `layer` ties a band to the authority hierarchy so that role-to-level
    consistency can be validated at creation time.
    """

    name = models.CharField(max_length=80)
    code = models.CharField(max_length=20, unique=True)
    layer = models.PositiveSmallIntegerField(choices=Layer.choices, db_index=True)
    rank = models.PositiveSmallIntegerField(
        default=0, help_text="Ordering within a layer. Higher = more senior."
    )

    class Meta:
        ordering = ["layer", "-rank", "name"]

    def __str__(self) -> str:
        return self.name


class Team(BaseModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="teams")
    parent_team = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    head_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="heading_teams",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
