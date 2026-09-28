"""
Support access: a customer lets an operator see its configuration, briefly.

THE SHAPE, AND THE ONE PLACE IT DIFFERS FROM THE ORIGINAL DESIGN.

The design said an approved grant "resolves to a read-only context". Taken
literally that means `resolve_context` choosing an organization for a platform
principal from something the request carries -- a grant id in a header, say --
which is client-supplied tenant identity by another name: the shape of
HRMS-INC-20260717-01, reintroduced for the sake of a support feature. So the
operator never gets a tenant context at all. An approved grant opens exactly
one platform route, which binds the organization FROM THE GRANT ROW on the
server and reads a fixed manifest. The operator's context stays grant-less
everywhere else, and the core authority code is not touched.

THE RULES, each enforced here and each tested:

  * requested by an operator, with a reason of at least 20 characters;
  * decided by an ADMIN OF THAT ORGANIZATION -- never by the operator;
  * usable for 24 hours from approval, by the operator who asked, only;
  * shows CONFIGURATION ONLY: `SUPPORT_VISIBLE`, a code constant, so no
    settings screen can widen it. No employees, salaries, payslips,
    candidates, documents or attendance;
  * audited in the CUSTOMER's trail at request, decision, every use and
    revocation -- "who looked at our setup, and why" is their question.

Full-read support access stays deferred until there is a named need and a
customer-facing consent screen that says what "full" means.
"""

from __future__ import annotations

from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from core.fields import EncryptedCharField
from core.middleware import acting_as

#: How long an approval lasts.
GRANT_HOURS = 24

#: What an approved grant shows. Configuration an operator needs to answer "why
#: does the leave balance look wrong" -- and nothing that describes a person.
#: Enumerated, never derived: adding a model to the product must not quietly
#: add it to what an operator can read.
SUPPORT_VISIBLE: tuple[str, ...] = (
    "organization.OrgSettings",
    "organization.Department",
    "organization.Designation",
    "organization.Location",
    "organization.EmployeeLevel",
    "organization.Team",
    "accounts.Role",
    "accounts.RolePermission",
    "workflows.HiringWorkflow",
    "workflows.WorkflowStage",
    "leave.LeaveType",
    "leave.LeavePolicy",
    "leave.HolidayCalendar",
    "leave.Holiday",
    "attendance.ShiftRule",
    "employees.DocumentType",
    "payroll.SalaryComponent",
)


class SupportError(Exception):
    """A support-access step refused before anything was written."""


def _audit(grant, *, actor, verb: str, event: str, **after):
    from apps.audit.events import record_event

    with acting_as(actor, organization=grant.organization):
        record_event(
            grant,
            actor=actor,
            entity_type="platform.SupportGrant",
            verb=verb,
            resource="",
            after={"event": event, "grant": str(grant.pk), **after},
            reason=grant.reason if event == "support_requested" else None,
        )


# ---------------------------------------------------------------------------
# The operator's side
# ---------------------------------------------------------------------------


def request_grant(organization, *, operator, reason: str):
    from apps.platform.models import SUPPORT_REASON_MIN_LENGTH, SupportGrant

    if not getattr(operator, "is_platform_admin", False):
        raise SupportError("Only a platform operator requests support access.")
    reason = (reason or "").strip()
    if len(reason) < SUPPORT_REASON_MIN_LENGTH:
        raise SupportError(
            f"Say why, in at least {SUPPORT_REASON_MIN_LENGTH} characters: the "
            f"customer's administrator decides on the strength of this sentence."
        )
    with acting_as(operator, organization=organization):
        grant = SupportGrant.objects.create(requested_by=operator, reason=reason)
    _audit(grant, actor=operator, verb="create", event="support_requested")
    return grant


def _operators_grant(grant_id, operator):
    """
    The grant, if it is THIS operator's. Another operator's grant answers as
    absent: approval was given to a named person, not to the platform.
    """
    from apps.platform.models import SupportGrant
    from core.access.platform_bypass import platform_bypass

    # Under row-level security the operator has no organization, so the grant
    # row -- which lives in the CUSTOMER's organization -- is read through the
    # one named door. Only this lookup crosses; the configuration reads that
    # follow run under normal RLS, bound to the grant's own organization.
    with transaction.atomic(), platform_bypass(
        reason="support access: load the operator's own grant", principal=operator
    ):
        return (
            SupportGrant.objects.all_orgs()
            .select_related("organization")
            .filter(pk=grant_id, requested_by=operator)
            .first()
        )


def configuration_snapshot(grant_id, *, operator) -> dict:
    """
    The customer's configuration, under an approved and unexpired grant.

    The organization comes from the GRANT ROW, not from the request -- see the
    module docstring. Every call is one audited use.
    """
    grant = _operators_grant(grant_id, operator)
    if grant is None:
        raise SupportError("No such support grant.")
    now = timezone.now()
    if not grant.is_usable(now):
        raise SupportError(
            "This grant is not usable: it has not been approved, or it has "
            "expired, been denied, or been revoked."
        )

    organization = grant.organization
    snapshot: dict[str, list[dict]] = {}
    # Read as the CONFINED runtime role, bound to the grant's organization,
    # even though this runs on a platform endpoint whose request is inside
    # `platform_bypass`. Support Access is the one platform feature that must
    # not see across customers, and this is what makes the DATABASE enforce
    # that rather than the tenant manager alone.
    from core.access.platform_bypass import as_runtime_role

    with as_runtime_role(), acting_as(operator, organization=organization):
        for label in SUPPORT_VISIBLE:
            model = apps.get_model(label)
            columns = [
                f.attname
                for f in model._meta.concrete_fields
                if not isinstance(f, EncryptedCharField)
            ]
            if hasattr(model.objects, "all_orgs"):
                # The tenant manager, bound to the grant's organization.
                rows = model.objects.all()
            else:
                # OrgSettings is organization-level but not a tenant model;
                # scoped explicitly, from the same grant row.
                rows = model.objects.filter(organization=organization)
            snapshot[label] = [
                {key: _jsonable(value) for key, value in row.items()}
                for row in rows.order_by("pk").values(*columns)
            ]

    _audit(
        grant, actor=operator, verb="export", event="support_access",
        tables=len(snapshot),
    )
    return {
        "organization": organization.slug,
        "grant": str(grant.pk),
        "expires_at": grant.expires_at.isoformat(),
        "tables": snapshot,
    }


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    return str(value)


def end_grant(grant_id, *, operator):
    """The operator finished early. Ends it the same way a revocation does."""
    from apps.platform.models import SupportGrantStatus

    grant = _operators_grant(grant_id, operator)
    if grant is None:
        raise SupportError("No such support grant.")
    if grant.status in (SupportGrantStatus.DENIED, SupportGrantStatus.REVOKED):
        return grant
    grant.status = SupportGrantStatus.REVOKED
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["status", "revoked_at", "updated_at"])
    _audit(grant, actor=operator, verb="update", event="support_ended_by_operator")
    return grant


# ---------------------------------------------------------------------------
# The customer's side
# ---------------------------------------------------------------------------


def decide(grant, *, admin, approve: bool):
    """
    The customer's Admin says yes or no. Only a REQUESTED grant is decided,
    and only once: a denied request is re-asked, not re-decided.
    """
    from apps.platform.models import SupportGrant, SupportGrantStatus

    # The tenant manager, bound to the grant's own organization: the customer
    # deciding needs no escape from tenancy, only a lock on their own row.
    with transaction.atomic(), acting_as(admin, organization=grant.organization):
        grant = SupportGrant.objects.select_for_update().get(pk=grant.pk)
        if grant.status != SupportGrantStatus.REQUESTED:
            raise SupportError(
                f"This request was already {grant.get_status_display().lower()}."
            )
        now = timezone.now()
        grant.decided_by = admin
        grant.decided_at = now
        if approve:
            grant.status = SupportGrantStatus.APPROVED
            grant.expires_at = now + timedelta(hours=GRANT_HOURS)
        else:
            grant.status = SupportGrantStatus.DENIED
        grant.save(
            update_fields=["status", "decided_by", "decided_at", "expires_at", "updated_at"]
        )
    _audit(
        grant, actor=admin,
        verb="approve" if approve else "reject",
        event="support_approved" if approve else "support_denied",
        expires_at=grant.expires_at.isoformat() if grant.expires_at else None,
    )
    return grant


def revoke(grant, *, admin):
    """The customer changed their mind. Effective on the operator's next call."""
    from apps.platform.models import SupportGrantStatus

    if grant.status != SupportGrantStatus.APPROVED:
        raise SupportError("Only an approved grant can be revoked.")
    grant.status = SupportGrantStatus.REVOKED
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["status", "revoked_at", "updated_at"])
    _audit(grant, actor=admin, verb="update", event="support_revoked")
    return grant
