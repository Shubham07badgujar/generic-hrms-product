"""
The Employee master.

An Employee is the PERSON; a User is the LOGIN. They are separate because the
two do not always coexist:

  - CEO and Admin are system-level roles with a login and no Employee record.
  - A person may exist in the org chart before their account is provisioned.

Everywhere else the two are created together in one transaction — see
`apps.employees.services.creation`. Authorization scope is derived from
`Employee.department` and `Employee.reporting_manager`, so a login without an
Employee resolves to no access for any role flagged `requires_employee`.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.fields import EncryptedCharField, mask_aadhaar, mask_bank_account, mask_pan
from core.models import OrgOwnedModel
from core.validators import STORED_PATH_MAX, scoped_storage_path


class EmploymentType(models.TextChoices):
    FULL_TIME = "full_time", "Full-time"
    PART_TIME = "part_time", "Part-time"
    CONTRACT = "contract", "Contract"
    INTERN = "intern", "Intern"
    CONSULTANT = "consultant", "Consultant"


class EmployeeStatus(models.TextChoices):
    """
    The employment lifecycle.

    Movement between these is NOT free-form — see
    `apps.employees.services.lifecycle`, which owns the permitted transitions.
    A status field anyone can set to anything is not a lifecycle, it is a
    comment box.
    """

    ONBOARDING = "onboarding", "Onboarding"
    ON_PROBATION = "on_probation", "On probation"
    ACTIVE = "active", "Active"
    CONFIRMED = "confirmed", "Confirmed"
    ON_LEAVE = "on_leave", "On long leave"
    ON_NOTICE = "on_notice", "On notice"
    RESIGNED = "resigned", "Resigned"
    TERMINATED = "terminated", "Terminated"
    EXITED = "exited", "Exited"


class ProbationStatus(models.TextChoices):
    """
    Probation, tracked separately from employment status.

    Two fields rather than one because they answer different questions —
    "are they employed?" and "has their probation been decided?" — and an
    employee can be ACTIVE while their probation is still DUE. Collapsing them
    would make "confirmed" ambiguous.

    There is deliberately no automatic path to CONFIRMED. The system moves
    ACTIVE → DUE on its own, and stops. Everything past that is an HR decision.
    """

    NOT_APPLICABLE = "not_applicable", "Not applicable"
    ACTIVE = "active", "In probation"
    DUE = "due", "Review due"
    CONFIRMED = "confirmed", "Confirmed"
    EXTENDED = "extended", "Extended"
    TERMINATED = "terminated", "Terminated in probation"


class Gender(models.TextChoices):
    MALE = "M", "Male"
    FEMALE = "F", "Female"
    OTHER = "O", "Other"


class Employee(OrgOwnedModel):
    employee_code = models.CharField(max_length=20, unique=True, db_index=True)

    #: The login, when one exists. SET_NULL rather than CASCADE: deleting an
    #: account must never delete the employment record it points at.
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee",
    )

    # --- identity ---
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    personal_email = models.EmailField(blank=True)
    work_email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.OTHER)
    photo = models.ImageField(
        upload_to="employees/photos/", max_length=STORED_PATH_MAX, null=True, blank=True
    )

    # --- statutory identifiers, encrypted at rest ---
    pan = EncryptedCharField(blank=True, plaintext_max_length=10)
    aadhaar = EncryptedCharField(blank=True, plaintext_max_length=12)
    uan = models.CharField(max_length=20, blank=True)
    esic_number = models.CharField(max_length=20, blank=True)

    # --- banking, encrypted at rest ---
    bank_account_number = EncryptedCharField(blank=True, plaintext_max_length=20)
    bank_ifsc = models.CharField(max_length=11, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)

    # --- placement in the organization ---
    department = models.ForeignKey(
        "organization.Department", on_delete=models.PROTECT, related_name="employees"
    )
    designation = models.ForeignKey(
        "organization.Designation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    location = models.ForeignKey(
        "organization.Location",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employees",
    )
    level = models.ForeignKey(
        "organization.EmployeeLevel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employees",
    )
    team = models.ForeignKey(
        "organization.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    reporting_manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
        db_index=True,
    )

    # --- employment ---
    employment_type = models.CharField(
        max_length=20, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    #: The annual CTC agreed AT HIRE — informational, filled by the Add
    #: Employee form or copied from the accepted offer at conversion. The
    #: payroll module's effective-dated SalaryStructure remains the sole
    #: authority for what is actually paid; this field never feeds a payslip.
    #: Exposed only to holders of SALARY/VIEW (and the person themselves).
    annual_ctc = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    date_of_joining = models.DateField()
    probation_start_date = models.DateField(null=True, blank=True)
    probation_end_date = models.DateField(null=True, blank=True)
    #: Set ONLY by an explicit HR confirmation. Nothing in this system writes
    #: it on a timer — see `services.probation`.
    confirmation_date = models.DateField(null=True, blank=True)
    probation_status = models.CharField(
        max_length=20,
        choices=ProbationStatus.choices,
        default=ProbationStatus.NOT_APPLICABLE,
        db_index=True,
    )
    date_of_exit = models.DateField(null=True, blank=True)
    notice_period_days = models.PositiveSmallIntegerField(default=30)
    status = models.CharField(
        max_length=20, choices=EmployeeStatus.choices, default=EmployeeStatus.ACTIVE, db_index=True
    )

    #: The candidate this employee was hired from, when they came through
    #: recruitment. Closes the loop from hire back to application, and is what
    #: makes a candidate permanently ineligible for the DPDP retention purge:
    #: their personal data is now employment data, governed by a different
    #: retention rule. PROTECT, so a purge can never orphan a hire.
    created_from_candidate = models.ForeignKey(
        "recruitment.Candidate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="hired_as",
    )

    class Meta:
        ordering = ["employee_code"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["reporting_manager"]),
            models.Index(fields=["status", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.employee_code} — {self.full_name}"

    # -- derived ----------------------------------------------------------

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def state(self) -> str:
        """Location state, which drives Professional Tax and holidays."""
        return self.location.state if self.location else ""

    @property
    def pan_masked(self) -> str:
        return mask_pan(self.pan)

    @property
    def aadhaar_masked(self) -> str:
        return mask_aadhaar(self.aadhaar)

    @property
    def bank_account_masked(self) -> str:
        return mask_bank_account(self.bank_account_number)

    # -- validation -------------------------------------------------------

    def clean(self):
        super().clean()
        self._assert_manager_is_not_self()
        self._assert_no_reporting_cycle()

    def _assert_manager_is_not_self(self) -> None:
        if self.reporting_manager_id and self.reporting_manager_id == self.pk:
            raise ValidationError(
                {"reporting_manager": "An employee cannot report to themselves."}
            )

    def _assert_no_reporting_cycle(self) -> None:
        """
        Walk the management chain upward looking for this employee.

        The access engine's reporting-tree walk is cycle-safe defensively, but
        a cycle in the org chart is corrupt data regardless: it means nobody in
        the loop has a real manager, and "who approves this leave request?" has
        no answer.
        """
        if not self.reporting_manager_id or not self.pk:
            return
        seen = {self.pk}
        node = self.reporting_manager
        while node is not None:
            if node.pk in seen:
                raise ValidationError(
                    {
                        "reporting_manager": (
                            "This reporting line would create a management cycle."
                        )
                    }
                )
            seen.add(node.pk)
            node = node.reporting_manager

    def reporting_tree_ids(self, *, include_self: bool = True) -> set:
        """Everyone at or below this employee. Breadth-first, cycle-safe."""
        found = {self.pk}
        frontier = [self.pk]
        while frontier:
            children = list(
                Employee.objects.filter(reporting_manager_id__in=frontier)
                .exclude(pk__in=found)
                .values_list("pk", flat=True)
            )
            if not children:
                break
            found.update(children)
            frontier = children
        if not include_self:
            found.discard(self.pk)
        return found


class EmployeeAddress(OrgOwnedModel):
    class Kind(models.TextChoices):
        CURRENT = "current", "Current"
        PERMANENT = "permanent", "Permanent"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="addresses")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    pincode = models.CharField(max_length=6)
    country = models.CharField(max_length=80, default="India")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "kind"], name="uniq_address_per_kind")
        ]


class EmergencyContact(OrgOwnedModel):
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="emergency_contacts"
    )
    name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=60)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)


class EmployeeEducation(OrgOwnedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="education")
    degree = models.CharField(max_length=150)
    institution = models.CharField(max_length=200)
    year_of_passing = models.PositiveSmallIntegerField(null=True, blank=True)
    grade = models.CharField(max_length=40, blank=True)


class EmployeeExperience(OrgOwnedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="experience")
    company = models.CharField(max_length=200)
    designation = models.CharField(max_length=150)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)


def next_employee_code(organization) -> str:
    """
    Allocate the next sequential employee code for ONE organization.

    Uses SELECT FOR UPDATE on that organization's settings row so two
    concurrent creations cannot claim the same code — a real risk during a bulk
    import.

    The organization is passed EXPLICITLY rather than read from the acting
    context. The previous version locked
    `OrgSettings.objects.select_for_update().first()`, which with more than one
    tenant would lock an arbitrary company's row and hand out ITS next code: a
    silent cross-tenant write in the middle of a hire, and one that would have
    surfaced as two companies' employee numbering mysteriously interleaving.
    Requiring an argument makes that impossible to express.
    """
    from apps.organization.models import OrgSettings

    org_id = getattr(organization, "pk", organization)
    if org_id is None:
        raise ValueError(
            "next_employee_code() needs an organization: employee numbering is "
            "per-company, and there is no sensible default."
        )

    # Ensure the row exists before locking it — SELECT FOR UPDATE cannot lock
    # a row that is not there, and a first hire is exactly when it may not be.
    OrgSettings.for_org(org_id)
    settings_row = OrgSettings.objects.select_for_update().get(organization_id=org_id)

    # Six digits by convention: EMP000101, EMP000102, … (prefix and next
    # number are org settings)
    code = f"{settings_row.employee_code_prefix}{settings_row.employee_code_next:06d}"
    settings_row.employee_code_next += 1
    settings_row.save(update_fields=["employee_code_next", "updated_at"])
    return code


# ===========================================================================
# Documents
# ===========================================================================


class DocumentCategory(models.TextChoices):
    IDENTITY = "identity", "Identity"
    ADDRESS = "address", "Address"
    EDUCATION = "education", "Education"
    EXPERIENCE = "experience", "Experience"
    EMPLOYMENT = "employment", "Employment"
    COMPLIANCE = "compliance", "Compliance"
    JOINING = "joining", "Joining"
    COMPANY_LETTER = "company_letter", "Company letter"
    OTHER = "other", "Other"


class DocumentType(OrgOwnedModel):
    """
    The catalogue of documents the organisation asks for.

    Configuration, not code: an onboarding checklist names document types by
    row, so adding "Police verification" is a data change. `is_mandatory` here
    is the org-wide default; a template item may still mark a specific document
    optional for a specific role.
    """

    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=60, unique=True)
    category = models.CharField(
        max_length=30, choices=DocumentCategory.choices, default=DocumentCategory.OTHER
    )
    description = models.CharField(max_length=255, blank=True)
    is_mandatory = models.BooleanField(default=False)
    requires_expiry = models.BooleanField(
        default=False, help_text="Passports and visas expire; degree certificates do not."
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["category", "order", "name"]

    def __str__(self) -> str:
        return self.name


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Awaiting verification"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


def employee_document_path(instance, filename: str) -> str:
    """
    Files are stored under an unguessable per-employee path.

    Not a security boundary on its own — downloads go through an authorising
    view, never a public URL — but it means a leaked storage listing does not
    hand out a tidy index of identity documents by employee code.
    """
    return scoped_storage_path("employee-documents", instance.employee_id, filename)


class EmployeeDocument(OrgOwnedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name="documents"
    )
    file = models.FileField(upload_to=employee_document_path, max_length=STORED_PATH_MAX)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)

    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        db_index=True,
    )
    #: Verification is a SEPARATE act from upload, by a different person. An
    #: employee uploading their own PAN card does not make it verified.
    verified_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    #: Rejection is recorded on its OWN fields rather than reusing the
    #: verification pair. Storing "who rejected this" in a column called
    #: `verified_by` is the kind of overload that reads fine while you are
    #: writing it and misleads everyone afterwards — including an auditor
    #: asking who attested to a document.
    rejected_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    #: Up to 250 words, so a character field is the wrong shape. Write-once:
    #: `services.documents.reject_document` refuses to touch a rejection that
    #: already exists, and a corrected document arrives as a NEW row.
    rejection_reason = models.TextField(blank=True)

    issue_date = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["status", "expires_on"]),
            # The management page's default view: newest first, filtered by
            # status, across every employee.
            models.Index(fields=["status", "-uploaded_at"]),
        ]
        constraints = [
            # A rejection without a reason is useless to the person who has to
            # act on it. Enforced here as well as in the service, because the
            # service is one caller among several possible ones and a shell or
            # a data migration is not obliged to go through it.
            models.CheckConstraint(
                condition=~models.Q(status=VerificationStatus.REJECTED)
                | ~models.Q(rejection_reason=""),
                name="ck_document_rejection_has_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} - {self.document_type.name}"

    @property
    def is_verified(self) -> bool:
        return self.status == VerificationStatus.VERIFIED


# ===========================================================================
# Probation
# ===========================================================================


class ProbationDecision(models.TextChoices):
    PENDING = "pending", "Awaiting HR decision"
    CONFIRM = "confirm", "Confirm"
    EXTEND = "extend", "Extend"
    TERMINATE = "terminate", "Terminate"


class ProbationReview(OrgOwnedModel):
    """
    The record of a probation being assessed and decided.

    Two roles, deliberately separated on one row: a REVIEWER (usually the
    reporting manager) records the assessment and a RECOMMENDATION, and HR
    records the DECISION. They are different fields, different people and
    different timestamps, because a manager's "confirm" is advice and HR's
    "confirm" is the employment decision - the same distinction the recruitment
    engine draws between a department recommendation and an HR final decision.

    The system never writes `decision` on its own.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="probation_reviews"
    )
    #: Snapshot of the date under review, so extending probation later does not
    #: rewrite what this review was about.
    probation_end_date = models.DateField()

    # --- assessment (reviewer) ---
    reviewer = models.ForeignKey(
        "employees.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    performance_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    reliability_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    role_specific_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    strengths = models.TextField(blank=True)
    areas_for_improvement = models.TextField(blank=True)
    recommendation = models.CharField(
        max_length=20, choices=ProbationDecision.choices, default=ProbationDecision.PENDING
    )
    reviewer_notes = models.TextField(blank=True)

    # --- decision (HR) ---
    decision = models.CharField(
        max_length=20,
        choices=ProbationDecision.choices,
        default=ProbationDecision.PENDING,
        db_index=True,
    )
    decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    #: Required for EXTEND and TERMINATE. A confirmation needs no defence; the
    #: other two affect someone's livelihood and must be explained.
    rationale = models.TextField(blank=True)
    extended_to = models.DateField(null=True, blank=True)
    confirmation_letter = models.ForeignKey(
        "onboarding.EmployeeLetter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # --- reminders (idempotency flags, not schedule state) ---
    notified_30d = models.BooleanField(default=False)
    notified_7d = models.BooleanField(default=False)
    notified_overdue_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-probation_end_date"]
        indexes = [
            models.Index(fields=["decision", "probation_end_date"]),
            models.Index(fields=["employee", "-created_at"]),
        ]
        constraints = [
            # An extension without a new date is not an extension.
            models.CheckConstraint(
                condition=~models.Q(decision="extend") | models.Q(extended_to__isnull=False),
                name="ck_probation_extend_requires_a_new_date",
            ),
        ]

    def __str__(self) -> str:
        return f"Probation review - {self.employee.employee_code} - {self.decision}"

    @property
    def is_decided(self) -> bool:
        return self.decision != ProbationDecision.PENDING
