"""
Explicit payroll audit events.

The implementation moved to `apps.audit.events` once a second caller needed it.
It belongs there: it already imported from `apps.audit`, and `core/` deliberately
does not import from `apps/`.

This module stays as a thin shim so the payroll call sites do not churn inside
an unrelated change, and so `resource` keeps defaulting to PAYROLL_RUN for them
— a default that would be wrong for any other caller, which is exactly why the
shared version makes it required.
"""

from __future__ import annotations

from apps.audit.events import VERBS, record_event  # noqa: F401  (re-exported)
from core.access.catalog import Resource


def audit_event(
    instance,
    *,
    actor,
    entity_type: str,
    verb: str,
    after: dict,
    before: dict | None = None,
    resource: str = Resource.PAYROLL_RUN,
    reason: str | None = None,
) -> None:
    record_event(
        instance,
        actor=actor,
        entity_type=entity_type,
        verb=verb,
        resource=resource,
        after=after,
        before=before,
        reason=reason or None,
    )
