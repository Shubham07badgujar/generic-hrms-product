"""
Automatic create/update/delete auditing via model signals.

Signals give complete coverage of ORM writes with no per-call-site discipline.
They do NOT cover `queryset.update()` or raw SQL — those bypass signals
entirely — so any bulk mutation that matters must write its own audit entry.

Business events with their own authority (candidate rejection, payroll
approval, asset allocation) are audited explicitly in their service functions,
because the semantic action matters more than the field diff.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid

from django.db.models.signals import post_delete, post_save, pre_save

from .registry import options_for, registered_models

logger = logging.getLogger("hrms.audit")

#: Never worth recording — pure noise on every write.
SKIP_FIELDS = {"updated_at", "created_at", "last_login", "last_login_at"}

REDACTED = "***"


def _serialize(value):
    """JSON-safe representation preserving enough fidelity to be useful."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    return str(value)


def _field_values(instance) -> dict:
    """
    Snapshot concrete fields.

    Uses `attname`, so FKs are captured as `department_id` rather than
    triggering a query per relation — auditing must never add N queries to a
    write.
    """
    values = {}
    for field in instance._meta.concrete_fields:
        if field.attname in SKIP_FIELDS or field.name in SKIP_FIELDS:
            continue
        values[field.attname] = _serialize(getattr(instance, field.attname, None))
    return values


def on_pre_save(sender, instance, **kwargs):
    """Stash the pre-write state so post_save can diff against it."""
    if instance.pk is None:
        instance.__dict__["_audit_previous"] = None
        return
    previous = sender.objects.filter(pk=instance.pk).first()
    instance.__dict__["_audit_previous"] = _field_values(previous) if previous else None


def on_post_save(sender, instance, created, **kwargs):
    from .models import AuditAction

    redact = options_for(sender)["redact"]
    current = _field_values(instance)

    if created:
        after = {k: (REDACTED if k in redact else v) for k, v in current.items()}
        _write(sender, instance, AuditAction.CREATE, before=None, after=after)
        return

    previous = instance.__dict__.get("_audit_previous")
    if previous is None:
        return

    before, after = {}, {}
    for key, new_value in current.items():
        old_value = previous.get(key)
        if old_value == new_value:
            continue
        if key in redact:
            before[key], after[key] = REDACTED, REDACTED
        else:
            before[key], after[key] = old_value, new_value

    # A save that changed nothing meaningful is not an event.
    if not after:
        return

    _write(sender, instance, AuditAction.UPDATE, before=before, after=after)


def on_post_delete(sender, instance, **kwargs):
    from .models import AuditAction

    redact = options_for(sender)["redact"]
    before = {
        k: (REDACTED if k in redact else v) for k, v in _field_values(instance).items()
    }
    _write(sender, instance, AuditAction.DELETE, before=before, after=None)


#: Attribute paths tried, in order, to find the employee an event is ABOUT.
#:
#: Most-specific first. `employee` covers the overwhelming majority — documents,
#: onboarding items, payslips, allocations — and the rest are the models that
#: reach an employee in one further hop. A path that does not resolve is
#: skipped, so a new model needs no change here unless it hides its employee
#: deeper than these.
SUBJECT_PATHS = (
    "employee",
    "exit_workflow.employee",
    "payslip.employee",
    "onboarding.employee",
    "salary_structure.employee",
    "email_account.employee",
    "asset_allocation.employee",
)

#: Field names a service may carry a mandatory justification in, in order.
REASON_KEYS = (
    "reason", "reason_text", "rationale", "reversal_reason",
    "notice_waiver_reason", "rejection_reason", "cancelled_reason",
)


def resolve_subject(instance):
    """
    The Employee this event is about, or None.

    Deliberately tolerant: any failure to resolve returns None rather than
    raising, because a scoping hint is never worth failing the write it
    describes. None NARROWS visibility to ALL scope only, so the failure mode
    is less disclosure rather than more.
    """
    from apps.employees.models import Employee

    if isinstance(instance, Employee):
        return instance

    for path in SUBJECT_PATHS:
        try:
            value = instance
            for part in path.split("."):
                value = getattr(value, part, None)
                if value is None:
                    break
            if isinstance(value, Employee):
                return value
        except Exception:
            continue
    return None


def extract_reason(after) -> str:
    """Lift a mandatory justification out of the payload so audit can show it."""
    if not isinstance(after, dict):
        return ""
    for key in REASON_KEYS:
        value = after.get(key)
        if isinstance(value, str) and value.strip():
            return value[:2000]
    return ""


def _write(sender, instance, action, *, before, after):
    from core.middleware import get_current_user, get_request_id

    from .models import AuditLog

    actor = get_current_user()
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None

    try:
        AuditLog.objects.create(
            actor=actor,
            actor_email=getattr(actor, "email", "") or "",
            action=action,
            entity_type=f"{sender._meta.app_label}.{sender.__name__}",
            entity_id=str(instance.pk),
            entity_label=str(instance)[:255],
            subject_employee=resolve_subject(instance),
            reason=extract_reason(after),
            before=before,
            after=after,
            request_id=get_request_id(),
        )
    except Exception:
        # Auditing must never take down the write it is recording. Log loudly
        # instead — a missing audit row is a serious problem, but a failed
        # payroll approval because the audit table was full is worse.
        logger.exception(
            "audit.write_failed entity=%s.%s id=%s action=%s",
            sender._meta.app_label,
            sender.__name__,
            instance.pk,
            action,
        )


def connect_for(model) -> None:
    label = f"{model._meta.app_label}.{model.__name__}"
    pre_save.connect(on_pre_save, sender=model, dispatch_uid=f"audit:pre:{label}")
    post_save.connect(on_post_save, sender=model, dispatch_uid=f"audit:post:{label}")
    post_delete.connect(on_post_delete, sender=model, dispatch_uid=f"audit:del:{label}")


def connect_all() -> None:
    for model in registered_models():
        connect_for(model)
