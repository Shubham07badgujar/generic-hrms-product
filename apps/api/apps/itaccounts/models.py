"""
Company email accounts.

THE PASSWORD IS NOT HERE, AND THERE IS NO FIELD FOR IT.

That is the whole design. V1 does not integrate with Google Workspace or
Microsoft 365 — HR creates the mailbox in the provider's own console and
RECORDS it here. The system tracks that an account exists, who provisioned it
and when; the credential travels between the provider and the employee by a
channel this application never touches.

A password column would be a liability with no compensating benefit: it cannot
be used to authenticate anything here, it cannot be shown through an API
without becoming an exfiltration target, and encrypting it only moves the
problem to key custody. `external_account_id` reserves the seam for a future
integration, which would use service-account delegation rather than a stored
user password.
"""

from __future__ import annotations

from django.db import models

from core.models import OrgOwnedModel


class EmailProvider(models.TextChoices):
    GOOGLE_WORKSPACE = "google_workspace", "Google Workspace"
    MICROSOFT_365 = "microsoft_365", "Microsoft 365"
    ZOHO = "zoho", "Zoho Mail"
    OTHER = "other", "Other"


class AccountStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    PROVISIONING = "provisioning", "Being provisioned"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    DEPROVISIONED = "deprovisioned", "Deprovisioned"


class CompanyEmailAccount(OrgOwnedModel):
    employee = models.OneToOneField(
        "employees.Employee", on_delete=models.PROTECT, related_name="email_account"
    )
    email_address = models.EmailField(db_index=True)
    provider = models.CharField(
        max_length=30, choices=EmailProvider.choices, default=EmailProvider.GOOGLE_WORKSPACE
    )
    #: The provider's own identifier for this mailbox. Reserves the integration
    #: seam so a future sync can adopt existing rows without a migration.
    external_account_id = models.CharField(max_length=160, blank=True)

    status = models.CharField(
        max_length=20, choices=AccountStatus.choices, default=AccountStatus.REQUESTED,
        db_index=True,
    )

    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    requested_at = models.DateTimeField(null=True, blank=True)
    #: Recorded when HR confirms the mailbox exists in the provider's console.
    provisioned_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    provisioned_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    deprovisioned_at = models.DateTimeField(null=True, blank=True)

    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email_address"], name="uniq_emailaccount_org_address"
            ),
        ]
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return self.email_address

    @property
    def is_live(self) -> bool:
        return self.status == AccountStatus.ACTIVE
