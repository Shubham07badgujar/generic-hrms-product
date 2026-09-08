"""
Notifications.

Delivery is polling at launch. The shape here is what makes a WebSocket
transport a later addition rather than a rewrite: a notification is a ROW
first and a delivery second. Creating one writes the row and then hands it to
whatever transports are configured; the row is the source of truth, so a
transport that did not exist when the row was written can pick it up
afterwards, and a transport that fails loses a delivery rather than an event.

Scoping is by RECIPIENT USER, not by employee. Admin and CEO have no Employee
record and must still receive notifications, so every query filters on
`recipient=request.user` rather than going through the employee-path scoper.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import BaseModel


class NotificationKind(models.TextChoices):
    """
    Every kind maps to a real decision point in an existing workflow.

    Nothing here fires on "something was created" for its own sake — a
    notification that does not change what its recipient does next is noise,
    and noise is how people learn to ignore the ones that matter.
    """

    # --- recruitment ---
    INTERVIEW_SCHEDULED = "interview_scheduled", "Interview scheduled"
    #: A candidate chose an interview window; someone who can schedule must
    #: confirm it by booking the interview.
    INTERVIEW_SLOT_SELECTED = "interview_slot_selected", "Interview slot chosen"
    INTERVIEW_RESCHEDULED = "interview_rescheduled", "Interview rescheduled"
    INTERVIEW_FEEDBACK_DUE = "interview_feedback_due", "Interview feedback due"
    DEPARTMENT_DECISION_RECORDED = (
        "department_decision_recorded", "Department decision awaiting HR",
    )
    CANDIDATE_REJECTED = "candidate_rejected", "Candidate rejected"
    CANDIDATE_SELECTED = "candidate_selected", "Candidate selected"
    OFFER_SENT = "offer_sent", "Offer sent"
    OFFER_ACCEPTED = "offer_accepted", "Offer accepted"

    # --- employee lifecycle ---
    ONBOARDING_TASK_ASSIGNED = "onboarding_task_assigned", "Onboarding task assigned"
    ONBOARDING_OVERDUE = "onboarding_overdue", "Onboarding task overdue"
    PROBATION_REVIEW_DUE = "probation_review_due", "Probation review due"
    PROBATION_DECIDED = "probation_decided", "Probation decision recorded"
    DOCUMENT_EXPIRING = "document_expiring", "Document expiring"
    #: Something is waiting in the verification queue. Addressed to the people
    #: who can actually clear it, not to everyone entitled to see documents.
    DOCUMENT_UPLOADED = "document_uploaded", "Document awaiting verification"
    #: Addressed to the ONE employee whose document it is, and to nobody else.
    #: That a named person's paperwork was refused, and why, is theirs.
    DOCUMENT_REJECTED = "document_rejected", "Your document was rejected"

    # --- leave ---
    #: A request landed in the deciding queue (HR, or Admin for Heads); the
    #: reporting manager's copy is informational.
    LEAVE_SUBMITTED = "leave_submitted", "Leave request submitted"
    #: Addressed to the ONE employee whose request it is.
    LEAVE_DECIDED = "leave_decided", "Your leave was decided"

    # --- offboarding ---
    RESIGNATION_SUBMITTED = "resignation_submitted", "Resignation submitted"
    CLEARANCE_TASK_ASSIGNED = "clearance_task_assigned", "Exit clearance task assigned"
    EXIT_APPROVED = "exit_approved", "Exit approved"

    # --- payroll ---
    PAYROLL_PROCESSED = "payroll_processed", "Payroll processed and awaiting review"
    PAYROLL_APPROVED = "payroll_approved", "Payroll approved"
    PAYROLL_REVERSED = "payroll_reversed", "Payroll reversed"
    PAYSLIP_AVAILABLE = "payslip_available", "Payslip available"
    STATUTORY_VERIFICATION_DUE = (
        "statutory_verification_due", "Statutory rates awaiting verification",
    )

    # --- assets and accounts ---
    ASSET_ALLOCATED = "asset_allocated", "Asset allocated"
    ASSET_RETURN_DUE = "asset_return_due", "Asset return due"
    EMAIL_ACCOUNT_READY = "email_account_ready", "Company account ready"

    # --- administrative ---
    ROLE_CHANGED = "role_changed", "Your access changed"
    ADMIN_OVERRIDE = "admin_override", "Administrative override recorded"


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    #: Something is blocked or a deadline has passed. Never suppressible by
    #: preference — see `NotificationPreference`.
    CRITICAL = "critical", "Critical"


class Notification(BaseModel):
    """One thing one person needs to know about."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=40, choices=NotificationKind.choices, db_index=True)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL
    )

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    #: Relative SPA path — "/payroll/<id>", "/recruitment/applications/<id>".
    #: Relative on purpose: an absolute URL would bake in an environment.
    link_url = models.CharField(max_length=400, blank=True)

    entity_type = models.CharField(max_length=120, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    #: Collapses repeats of the same pending fact — a probation review that is
    #: still due tomorrow should not become a second row. Services pass a
    #: stable key and `notify()` skips a duplicate that is still unread.
    dedupe_key = models.CharField(max_length=200, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The polling query, exactly: my unread, newest first.
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            models.Index(fields=["recipient", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id}: {self.title}"


class NotificationPreference(BaseModel):
    """
    Per-user, per-kind delivery choice.

    Absence means "on" — a user who has never opened preferences still gets
    notified, because defaulting to silence would make the whole system depend
    on people discovering a settings screen.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    kind = models.CharField(max_length=40, choices=NotificationKind.choices)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=False)

    class Meta:
        ordering = ["kind"]
        constraints = [
            models.UniqueConstraint(fields=["user", "kind"], name="uniq_preference_per_kind")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.kind}"


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed by preference"


class NotificationDelivery(BaseModel):
    """
    One attempt to get a notification to someone through one channel.

    Separate from the notification so a failed email does not lose the in-app
    record, and so adding a WebSocket channel later means new rows rather than
    a schema change.
    """

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="deliveries"
    )
    channel = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["notification", "channel"])]

    def __str__(self) -> str:
        return f"{self.channel}:{self.status}"
