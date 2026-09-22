"""
Plans and subscriptions: what a customer bought, and what that permits.

TWO STATUS FIELDS, ONE AUTHORITY

The brief asks for both an organization lifecycle and a subscription lifecycle,
and two fields answering "is this organization live?" is exactly how they
diverge. The resolution, and it is not negotiable:

    Organization.status  is AUTHORITATIVE FOR ACCESS.
    Subscription.status  is COMMERCIAL STATE.

`resolve_context` reads the first and never the second. A subscription
transition WRITES `Organization.status` through one service, so there is a
single field to ask and a single path that answers it. Nothing else may write
`Organization.status` from here.

NO BILLING

No prices, no invoices, no payment provider. `Plan` carries what the product
must ENFORCE -- a seat limit and a set of disabled features -- plus a support
level that exists to be displayed. Everything commercial stays outside; this is
the seam it would attach to, not the thing itself.

WHY `disabled_features` AND NOT `enabled_features`

Absence must mean "allowed", so that adding a `FeatureCode` does not silently
switch a module off for every existing customer at deploy time. It is the
opposite convention from the permission grants map, where absence means deny,
and the difference is deliberate: an unlisted PERMISSION is authority nobody
granted, while an unlisted FEATURE is a module nobody sold separately.
"""

from __future__ import annotations

from django.db import models

from core.access.features import ALWAYS_ON, FeatureCode
from core.models import BaseModel, OrgOwnedModel


class SupportLevel(models.TextChoices):
    COMMUNITY = "community", "Community"
    STANDARD = "standard", "Standard"
    PRIORITY = "priority", "Priority"


class Plan(BaseModel):
    """
    A sellable tier. Platform-owned and shared by every customer on it.

    Deliberately NOT organization-owned: a plan is a description of what the
    SaaS offers, and one copy per customer would mean changing a plan's terms
    required rewriting N rows and hoping they agreed.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)

    #: Active employees permitted. NULL means unlimited, which is a real
    #: answer and not "unset" -- an enterprise agreement without a seat count
    #: is a normal thing to sell.
    employee_limit = models.PositiveIntegerField(null=True, blank=True)

    #: Features this plan does NOT include, as `FeatureCode` values. Absence
    #: means included -- see the module docstring.
    disabled_features = models.JSONField(default=list, blank=True)

    #: MODELLED, MEASURED, SURFACED -- NOT BLOCKING.
    #:
    #: A storage cap that silently breaks a payroll run's PDF generation is
    #: worse than no cap, and blocking uploads needs an eviction and appeal
    #: story that does not exist. So this is shown on the platform console and
    #: on the customer's own plan page, and enforced by a conversation.
    storage_limit_mb = models.PositiveIntegerField(null=True, blank=True)

    #: Display and triage only. No code enforces it, and that is the point of
    #: saying so here rather than leaving a reader to discover it.
    support_level = models.CharField(
        max_length=20, choices=SupportLevel.choices, default=SupportLevel.STANDARD
    )

    #: Offered to new customers. An existing subscription to a retired plan
    #: keeps working -- withdrawing a plan from sale is not the same as
    #: withdrawing it from the people who bought it.
    is_public = models.BooleanField(default=True)

    display_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return self.name

    def includes(self, feature) -> bool:
        """Whether this plan includes `feature`."""
        if feature in ALWAYS_ON:
            return True
        return str(feature) not in {str(f) for f in (self.disabled_features or [])}

    @property
    def enabled_features(self) -> list[str]:
        """Every feature this plan includes, for the SPA's snapshot."""
        return [str(f) for f in FeatureCode if self.includes(f)]

    def clean(self):
        from django.core.exceptions import ValidationError

        known = {str(f) for f in FeatureCode}
        unknown = sorted({str(f) for f in (self.disabled_features or [])} - known)
        if unknown:
            raise ValidationError(
                {"disabled_features": f"Not features: {', '.join(unknown)}."}
            )
        always_on = sorted(
            {str(f) for f in (self.disabled_features or [])}
            & {str(f) for f in ALWAYS_ON}
        )
        if always_on:
            raise ValidationError(
                {
                    "disabled_features": (
                        f"{', '.join(always_on)} cannot be disabled: the "
                        f"product does not work without it, so a plan without "
                        f"it would not be a cheaper HRMS but a broken one."
                    )
                }
            )


class SubscriptionStatus(models.TextChoices):
    """
    COMMERCIAL state. Not access -- see the module docstring.

    `PAST_DUE` deliberately does not stop anyone working. Locking an HR
    department out of payroll on the 30th because an invoice is late punishes
    the employees rather than the buyer; the warning goes in the snapshot and
    the conversation happens out of band.
    """

    TRIALING = "trialing", "Trialing"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


class Subscription(BaseModel):
    """
    One organization's plan, and the row a seat check locks.

    A `OneToOneField` rather than a history of rows: "which plan is this
    customer on" must have exactly one answer, and a table of overlapping
    periods is how that question starts needing a date to answer it. Plan
    CHANGES are audited, which is where the history lives.
    """

    organization = models.OneToOneField(
        "organization.Organization",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIALING,
        db_index=True,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    #: When a trial ends, or when a cancelled subscription stops. NULL means
    #: open-ended.
    ends_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    #: A per-organization override of the plan's seat limit, with a reason.
    #: NULL defers to the plan. Nothing may set this without the reason, which
    #: is why they are adjacent and why the platform service requires both:
    #: "why does this customer have 400 seats on a 50-seat plan" has to be
    #: answerable a year later.
    employee_limit_override = models.PositiveIntegerField(null=True, blank=True)
    override_reason = models.CharField(max_length=255, blank=True)

    #: Read and export of a module's existing data stay available for this
    #: many days after the module is switched off, so a customer can retrieve
    #: records they are statutorily obliged to keep. Payslips and audit rows
    #: are exactly the category that must not vanish on a downgrade.
    read_only_grace_days = models.PositiveSmallIntegerField(default=90)
    #: When the current plan's features last narrowed. The grace window is
    #: measured from here, not from the subscription's start.
    features_narrowed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(employee_limit_override__isnull=True)
                | ~models.Q(override_reason=""),
                name="subscription_override_needs_a_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization.slug} -> {self.plan.code}"

    @property
    def employee_limit(self):
        """Seats permitted: the override if there is one, else the plan's."""
        if self.employee_limit_override is not None:
            return self.employee_limit_override
        return self.plan.employee_limit

    def includes(self, feature) -> bool:
        return self.plan.includes(feature)

    @property
    def enabled_features(self) -> list[str]:
        return self.plan.enabled_features


class SupportGrantStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    DENIED = "denied", "Denied"
    REVOKED = "revoked", "Revoked"


#: The shortest reason a customer is asked to approve. Twenty characters is
#: not a quality bar -- it is the difference between "support" and a sentence
#: somebody can say yes or no to.
SUPPORT_REASON_MIN_LENGTH = 20


class SupportGrant(OrgOwnedModel):
    """
    A customer's time-limited, read-only consent to an operator seeing its
    CONFIGURATION -- never its people.

    Organization-owned, so the customer side is isolated by the tenant manager
    like every other customer record; the platform side reads it with
    `all_orgs()`, which is what a platform principal with no organization of
    its own has to do.

    The customer approves, not the operator: the platform has no implicit
    reach into customer data, and a grant the operator could approve for
    themselves would be exactly that reach with a form in front of it. What an
    approved grant shows is a code constant (`apps.platform.services.support.
    SUPPORT_VISIBLE`), so no settings screen can widen it.
    """

    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+"
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=SupportGrantStatus.choices,
        default=SupportGrantStatus.REQUESTED,
        db_index=True,
    )
    #: The customer's Admin who approved or denied it. SET_NULL because a
    #: purge removes the customer's logins and the grant's history outlives
    #: the person.
    decided_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason__regex=r"^.{%d,}" % SUPPORT_REASON_MIN_LENGTH),
                name="ck_support_grant_reason_length",
            ),
            # An approved grant always knows when it ends. A grant without an
            # expiry would be standing access, which is the thing this model
            # exists not to be.
            models.CheckConstraint(
                condition=~models.Q(status="approved") | models.Q(expires_at__isnull=False),
                name="ck_support_grant_approved_expires",
            ),
        ]

    def is_usable(self, now) -> bool:
        return (
            self.status == SupportGrantStatus.APPROVED
            and self.expires_at is not None
            and now < self.expires_at
        )
