"""
The tenant, and the organizational structure inside it.

`Organization` is the customer company: the row every other table in the
product ultimately belongs to. `OrgSettings` holds that company's operational
particulars, and departments/designations/locations/levels/teams describe its
shape.

See docs/ARCHITECTURE.md PART 13 for why tenancy returned and the constraints
it must satisfy.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.access.catalog import DepartmentKind, Layer
from core.fields import EncryptedCharField
from core.models import BaseModel, OrgOwnedModel
from core.validators import STORED_PATH_MAX


class OrgStatus(models.TextChoices):
    """
    Lifecycle of a customer organization.

    Authoritative for ACCESS. A subscription's commercial state is tracked
    separately, and a change there is applied to this field through one
    service, so there is a single field to ask "may these users work?" and a
    single write path that answers it.
    """

    PENDING_SETUP = "pending_setup", "Pending setup"
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"
    ARCHIVED = "archived", "Archived"


#: Statuses whose users may sign in and use the product. PENDING_SETUP is a
#: working state, not a locked one: the administrator is mid-wizard and must be
#: able to read and write in order to finish it.
OPERATIONAL_STATUSES = frozenset(
    {OrgStatus.PENDING_SETUP, OrgStatus.TRIAL, OrgStatus.ACTIVE}
)


class Organization(models.Model):
    """
    One customer company.

    Deliberately NOT a `BaseModel`, for two reasons.

    `BaseModel` carries `created_by`/`updated_by` foreign keys to the user
    model. `accounts` is the first local app and gains a link back to this one,
    so those columns would make the dependency circular and force the
    `0001_initial`/`0002_initial` split already visible in `apps/employees`.
    "Who created this organization" is answered by the audit log, which is the
    only append-only record in the system and the right place for it.

    `BaseModel` also carries `is_active`, which would be a second way to say
    "this organization is gone" alongside `status`. Two fields answering one
    question is how they come to disagree.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=250, blank=True)

    #: Stable public handle. The ONLY globally unique key in the product
    #: besides `User.email`, because the pre-authentication branding endpoint
    #: has no principal to resolve a tenant from and must be told which one.
    slug = models.SlugField(max_length=63, unique=True)

    logo = models.ImageField(
        upload_to="org/", max_length=STORED_PATH_MAX, null=True, blank=True
    )
    favicon = models.ImageField(
        upload_to="org/", max_length=STORED_PATH_MAX, null=True, blank=True
    )

    primary_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    website = models.URLField(blank=True)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    #: Matches `Location.state`, which drives Professional Tax jurisdiction.
    state = models.CharField(max_length=8, blank=True)
    country = models.CharField(max_length=2, default="IN")
    pincode = models.CharField(max_length=10, blank=True)

    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    currency = models.CharField(max_length=3, default="INR")
    date_format = models.CharField(max_length=32, default="d M Y")

    status = models.CharField(
        max_length=20,
        choices=OrgStatus.choices,
        default=OrgStatus.PENDING_SETUP,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_operational(self) -> bool:
        """Whether this organization's users may currently use the product."""
        return self.status in OPERATIONAL_STATUSES


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    REMOVED = "removed", "Removed"


class OrganizationMembership(BaseModel):
    """
    Which organization a user belongs to. The sole source of tenant identity.

    NOT an `OrgOwnedModel`, and the reason is structural rather than stylistic:
    resolving a principal's organization is what this table is queried FOR, so
    a manager that required an organization to be bound before it could be read
    would be circular. It is also why `User` carries no `organization` column —
    a denormalized copy is a cache, and a cache of tenant identity that can go
    stale is precisely the class of bug this design exists to prevent.

    Modelled as a genuine many-to-many so the identity model never has to be
    redesigned, then clamped to one active membership for V1. Removing that
    clamp is only the schema half of multi-organization support; see
    docs/ARCHITECTURE.md §13.4 for the rest.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="memberships"
    )
    status = models.CharField(
        max_length=20,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
        db_index=True,
    )
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="uniq_membership_org_user"
            ),
            # THE V1 CLAMP. One active membership per user, so a login resolves
            # to exactly one organization with no selection step. Dropping this
            # is the schema change multi-org needs; it is not the whole job.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status=MembershipStatus.ACTIVE, is_active=True),
                name="uniq_one_active_membership",
            ),
        ]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return f"{self.user} @ {self.organization}"


class OrgSettings(BaseModel):
    """
    One organization's operational particulars. Exactly one row PER
    organization.

    Kept separate from `Organization` rather than folded into it, for three
    reasons that all cut the same way.

    `employee_code_next` is a hot counter: `next_employee_code()` takes a
    SELECT FOR UPDATE row lock on it for every hire. `Organization` is the row
    every table in the product points at and every request reads, so putting a
    per-hire write lock on it would be contention to undo later.

    The statutory registrations below are India-specific. This is a generic
    HRMS product; the tenant table has to stay jurisdiction-agnostic, and this
    is where a second compliance profile would eventually go.

    And identity belongs on the tenant. Name, legal name, logo, currency and
    timezone moved to `Organization` -- there is one answer to "who is this
    company", and it is not on a settings row.
    """

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="settings"
    )

    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    tan = models.CharField(max_length=10, blank=True)
    cin = models.CharField(max_length=21, blank=True)
    epf_number = models.CharField(max_length=30, blank=True)
    esi_number = models.CharField(max_length=30, blank=True)

    financial_year_start_month = models.PositiveSmallIntegerField(default=4)

    employee_code_prefix = models.CharField(max_length=8, default="EMP")
    employee_code_next = models.PositiveIntegerField(default=1)

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
        return f"Settings for {self.organization}"

    @classmethod
    def for_org(cls, organization) -> OrgSettings:
        """
        This organization's settings, created on first access.

        Replaces the old `get()` singleton accessor. The rename is deliberate
        rather than cosmetic: `get()` compiled fine in a multi-tenant world and
        would have quietly returned an arbitrary company's row. Requiring an
        argument turns every one of those call sites into a compile-time
        question about WHICH organization is meant.
        """
        obj, _ = cls.objects.get_or_create(
            organization_id=getattr(organization, "pk", organization)
        )
        return obj


def current_organization():
    """
    The organization the running code is acting on behalf of, or None.

    TRANSITIONAL. Documents and correspondence belong to a specific company,
    but the HR tables do not carry the organization column yet, so there is no
    relational path from an Employee or a PayrollRun to its owner. Until there
    is, this reads the acting context -- which is bound for the whole of any
    authenticated request.

    Returns None rather than guessing when nothing is bound, and every caller
    falls back to a NEUTRAL label. Naming the wrong company on a payslip or an
    offer letter is considerably worse than naming none.
    """
    from core.middleware import get_current_org_id

    org_id = get_current_org_id()
    return Organization.objects.filter(pk=org_id).first() if org_id else None


def current_org_settings():
    """This organization's `OrgSettings`, or None when no organization is bound."""
    organization = current_organization()
    return OrgSettings.for_org(organization) if organization is not None else None


class Location(OrgOwnedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, db_index=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    #: Drives Professional Tax jurisdiction and the holiday calendar.
    state = models.CharField(max_length=8, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    is_head_office = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_location_org_code"
            ),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Department(OrgOwnedModel):
    """
    A department. `kind` groups it into one of the four functional areas the
    role hierarchy is built around.

    IMPORTANT: `kind` drives dashboards, BI grouping and role-compatibility
    validation. It does NOT drive authorization scope — that comes from
    `Employee.department`, so moving a person between departments changes what
    they can see without anyone touching their role.
    """

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, db_index=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_department_org_code"
            ),
        ]
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


class Designation(OrgOwnedModel):
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
                fields=["organization", "title", "department"], name="uniq_designation_title_department"
            )
        ]

    def __str__(self) -> str:
        return self.title


class EmployeeLevel(OrgOwnedModel):
    """
    Seniority band, aligned to the five-layer hierarchy.

    `layer` ties a band to the authority hierarchy so that role-to-level
    consistency can be validated at creation time.
    """

    name = models.CharField(max_length=80)
    code = models.CharField(max_length=20, db_index=True)
    layer = models.PositiveSmallIntegerField(choices=Layer.choices, db_index=True)
    rank = models.PositiveSmallIntegerField(
        default=0, help_text="Ordering within a layer. Higher = more senior."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_employeelevel_org_code"
            ),
        ]
        ordering = ["layer", "-rank", "name"]

    def __str__(self) -> str:
        return self.name


class Team(OrgOwnedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, db_index=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uniq_team_org_code"
            ),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OrgEmailConfig(BaseModel):
    """
    One organization's outbound mail settings.

    Every field is OPTIONAL and falls back to the deployment's, field by field.
    An organization that wants only its own from-address, leaving the SMTP
    server to the platform, sets one line -- rather than restating the whole
    block, which is how configuration screens end up full of copied-in values
    that go stale.

    Behind `ORG_SETTINGS`, which is the Admin's surface. Device credentials
    live on a different table behind a different permission on purpose: one
    table would have meant one permission, and an HR manager who may configure
    the attendance clock would thereby read the mail password.
    """

    organization = models.OneToOneField(
        "organization.Organization",
        on_delete=models.CASCADE,
        related_name="email_config",
    )

    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    use_tls = models.BooleanField(default=True)
    username = models.CharField(max_length=255, blank=True)
    #: Encrypted at rest, `write_only` on the way in, and never read back --
    #: the API reports `has_password` and nothing else. A masked value still
    #: leaks the length, and the reason to reveal a password is always better
    #: served by setting a new one.
    password = EncryptedCharField(max_length=255, blank=True, default="")

    #: The envelope sender. What recipients see, and what SPF and DKIM are
    #: checked against -- so an organization that sets this without also
    #: authorising the platform's mail server to send as their domain will see
    #: deliverability fall. Worth saying on the settings screen.
    from_email = models.EmailField(blank=True)
    #: Where a confused recipient replies. A human-facing address, not the one
    #: SMTP authenticates as.
    hr_contact = models.EmailField(blank=True)

    class Meta:
        verbose_name = "organization email configuration"

    def __str__(self) -> str:
        return f"{self.organization.slug} mail"

    @property
    def has_password(self) -> bool:
        return bool(self.password)
