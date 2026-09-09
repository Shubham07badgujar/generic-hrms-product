"""
Append-only audit log.

Not a BaseModel: this is a write-once sink, so soft delete and `updated_at`
would be meaningless, and a bigserial id is cheaper than a UUID at the volume
this table reaches.

Immutability is enforced twice — in Python here, and by revoking UPDATE/DELETE
from the application database role in production. Python alone is bypassable by
raw SQL; the grant alone gives a confusing error. Both together are clear and
enforced.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    CREATE = "create", "Created"
    UPDATE = "update", "Updated"
    DELETE = "delete", "Deleted"
    APPROVE = "approve", "Approved"
    # Distinct from DELETE: rejecting a candidate is a business decision with
    # its own authority (HR Head only), not a data removal.
    REJECT = "reject", "Rejected"
    # An administrative override of someone else's decision. Separate so it is
    # trivially filterable — these are the events a reviewer looks for first.
    OVERRIDE = "override", "Overridden"
    RECOMMEND = "recommend", "Recommended"
    REVERSE = "reverse", "Reversed"
    ALLOCATE = "allocate", "Allocated"
    RETURN = "return", "Returned"
    LOGIN = "login", "Signed in"
    LOGIN_FAILED = "login_failed", "Sign-in failed"
    ACCESS_PII = "access_pii", "Accessed sensitive data"
    EXPORT = "export", "Exported data"
    # Bulk ingest of externally sourced records. A distinct verb because
    # "where did these 1,900 candidates come from" is a question CREATE
    # cannot answer, and it is the first thing a reviewer asks.
    IMPORT = "import", "Imported"
    PERMISSION_CHANGE = "permission_change", "Permissions changed"
    ROLE_CHANGE = "role_change", "Roles changed"
    CREDENTIAL_ISSUE = "credential_issue", "Credential issued"
    CREDENTIAL_VIEW = "credential_view", "Credential viewed"


class AuditLog(models.Model):
    id = models.BigAutoField(primary_key=True)

    # Nullable: pre-authentication events (failed logins, bootstrap) have no
    # actor, and we still want them recorded.
    #: The organization this event belongs to. NULLABLE on purpose: a failed
    #: login for an unknown address, a platform-admin action and the creation
    #: of an organization itself genuinely have none.
    #:
    #: NULL means "platform-owned", NOT "everyone's". Any predicate over this
    #: column is `= X`, never `= X OR IS NULL` -- the second reading is how an
    #: audit trail becomes a cross-tenant read.
    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        db_index=True,
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    #: Denormalised so the record survives the user being deleted.
    actor_email = models.CharField(max_length=254, blank=True)

    action = models.CharField(max_length=30, choices=AuditAction.choices, db_index=True)
    #: The access-control resource, so audit can be filtered by the same
    #: vocabulary permissions use.
    resource = models.CharField(max_length=40, blank=True, db_index=True)

    entity_type = models.CharField(max_length=120, db_index=True)  # "app_label.Model"
    entity_id = models.CharField(max_length=64, db_index=True)
    entity_label = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable identifier captured at write time, so the log "
        "stays readable after the row is gone.",
    )

    #: The person this event is ABOUT, which is rarely the person who did it.
    #:
    #: Without this, audit could only ever be all-or-nothing: the row has no
    #: employee relation of its own, so a Department Head holding DEPARTMENT
    #: scope would resolve to NO rows rather than to their department's.
    #: Resolved generically at write time (`signals.resolve_subject`). NULL
    #: means "not about a specific person" — a config change, a role edit, a
    #: statutory rate — and those stay visible only at ALL scope, which is the
    #: right answer for organisation-level events.
    subject_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    #: Why, when the action carried a mandatory justification — rejections,
    #: overrides, reversals, notice waivers. Lifted out of `after` at write
    #: time so the viewer can show it as a column without having to know the
    #: payload shape each writer happens to use.
    reason = models.TextField(blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    request_id = models.UUIDField(null=True, blank=True, db_index=True)

    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["actor", "-occurred_at"]),
            models.Index(fields=["action", "-occurred_at"]),
            # The department-scoped viewer's query: subject's department, newest
            # first. Without it every Department Head paging audit scans the
            # whole table.
            models.Index(fields=["subject_employee", "-occurred_at"]),
            models.Index(fields=["resource", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.actor_email or 'system'} {self.action} {self.entity_type}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog is append-only; existing rows cannot be modified.")
        if self.organization_id is None:
            self.organization_id = self._current_organization_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _current_organization_id():
        """
        The organization this event belongs to, or None for a platform event.

        Stamped HERE rather than at each writer for one reason: fifteen call
        sites construct `AuditLog` directly, in addition to the signal path, and
        a rule spread over sixteen places is a rule the sixteenth breaks. It is
        the same argument as `OrgStampingMixin`, which stamps every other
        tenant-owned table in `save()` instead of asking its callers to.

        None is a real answer, not a failure: a failed login for an unknown
        address, the platform bootstrap, and the creation of an organization
        before its row exists all genuinely have no organization. Those rows are
        platform-owned, and `apply_org_predicate` shows them to nobody, because
        `= X` never matches NULL.
        """
        from core.middleware import get_current_org_id

        return get_current_org_id()

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog is append-only; rows cannot be deleted.")
