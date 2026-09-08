"""
Explicit audit events, for acts a reviewer searches for by name.

The signal registry records field-level changes on every registered model. This
is for the events where "PayrollRun.status changed from review to approved" is
technically the same information but nothing like as findable — approving a run,
reversing one, importing two thousand candidates.

It also covers the gap the signals cannot: `bulk_create` and `queryset.update()`
do not fire `post_save`, so any bulk mutation that matters has to write its own
entry. That is stated in `signals.py` and is why this exists as a shared helper
rather than staying where it was born, inside payroll.

`resource` is REQUIRED here, unlike in the payroll version this replaces. That
had `Resource.PAYROLL_RUN` as a default, which was a payroll bias that would
have silently mis-filed every new caller.
"""

from __future__ import annotations

#: Semantic verb -> AuditAction member name.
VERBS = {
    "create": "CREATE",
    "update": "UPDATE",
    "delete": "DELETE",
    "approve": "APPROVE",
    "reject": "REJECT",
    "reverse": "REVERSE",
    "export": "EXPORT",
    "import": "IMPORT",
}


def record_event(
    instance,
    *,
    actor,
    entity_type: str,
    verb: str,
    resource: str,
    after: dict,
    before: dict | None = None,
    reason: str | None = None,
) -> None:
    """
    Write one audit row for an act that the automatic auditor would under-describe.

    `reason` may be given explicitly because `extract_reason` scans a fixed set
    of keys in `after`, and would not find, say, a legal-basis attestation.
    """
    from apps.audit.models import AuditAction, AuditLog
    from apps.audit.signals import extract_reason, resolve_subject
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=getattr(AuditAction, VERBS[verb]),
        resource=resource,
        entity_type=entity_type,
        entity_id=str(instance.pk),
        entity_label=str(instance),
        # The same resolver the signal auditor uses, so an explicit event scopes
        # identically to an automatic one. An organisation-level event — a
        # payroll run, an import batch — correctly resolves to None, which keeps
        # it visible only at Scope.ALL.
        subject_employee=resolve_subject(instance),
        reason=reason if reason is not None else extract_reason(after),
        before=before or {},
        after=after,
        request_id=get_request_id(),
    )
