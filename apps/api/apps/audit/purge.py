"""
The one sanctioned rewrite of audit rows: scrubbing a purged organization.

`AuditLog` is append-only -- `save()` refuses an existing row and `delete()`
always raises -- and this module does not weaken either. It uses a queryset
UPDATE, which bypasses `save()`, and it exists in exactly one place, named,
called only by `purge_organization`, so the exception is as greppable as the
rule.

WHY THE ROWS ARE KEPT AND THEIR CONTENTS ARE NOT. The decision taken for purge
is "keep the trail, scrub the payloads": an operator must still be able to
answer what happened to a customer's data -- who did what, to which entity,
when -- after the customer is gone, while the customer's personal data does
not outlive the purge that was supposed to remove it. So:

  kept     id, organization, action, resource, entity_type, entity_id,
           request_id, occurred_at, and the actor when the actor was a
           platform operator (their actions are the platform's record)
  blanked  before, after, metadata, entity_label ("EMP001 -- Asha Rao"),
           reason, ip, user_agent, and actor_email for the customer's own
           users
  nulled   subject_employee, which points at a row this purge deletes

The terminal "organization_purged" record is written AFTER this runs, so it is
the one row in the trail whose payload survives -- it holds counts, not people.
"""

from __future__ import annotations

from .models import AuditLog


def scrub_for_purge(organization, *, customer_user_ids) -> int:
    """Blank the personal content of an organization's audit rows. Returns the count."""
    rows = AuditLog.objects.filter(organization=organization)
    scrubbed = rows.update(
        before=None,
        after=None,
        metadata={},
        entity_label="",
        reason="",
        ip=None,
        user_agent="",
        subject_employee=None,
    )
    # A customer user's address is personal data; a platform operator's is the
    # platform's own record of who acted, and stays.
    rows.filter(actor_id__in=list(customer_user_ids)).update(actor_email="")
    return scrubbed
