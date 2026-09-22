"""
The end of a customer: archive, then -- a year later, and deliberately -- purge.

    CANCELLED --(90 days: the export window)--> ARCHIVED --(365 days)--> purged

NEITHER STEP IS AUTOMATIC and neither is a single call that removes a customer.
Archive is a platform action with a reason, available in the console. Purge is
a management command on the server with a typed confirmation naming the
organization, and the command is the only door: there is deliberately no
button that deletes a customer.

ARCHIVE is a status change and nothing else. No row is touched; the customer's
users, already refused since cancellation, stay refused; and the export window
has closed by construction, because archiving is refused until it has.

PURGE is the only hard delete in the product. Everywhere else
`BaseModel.delete()` is a soft delete, and everywhere else is right to be.
What it removes and what it keeps was decided, not defaulted:

  removed  every row of the 94 organization-owned models; the organization's
           settings, email configuration and memberships; its users' logins;
           its file subtree under MEDIA_ROOT/organizations/<uuid>/
  kept     the Organization row itself, as a tombstone with `purged_at`;
           the Subscription (a commercial record, not personal data);
           the audit trail, with its payloads scrubbed (apps.audit.purge)
  spared   a login that certified or submitted a DEPLOYMENT-WIDE statutory
           rate set. Those rows reference it with PROTECT because the
           four-eyes record of India's PF/ESI/PT rates is every customer's,
           not this one's; such a login is deactivated with an unusable
           password instead of deleted, and the purge says so.

HOW THE DELETE IS DONE. Organization-owned tables point at one another with
PROTECT in 64 places, so `QuerySet.delete()` -- whose collector enforces
PROTECT in Python -- cannot remove a whole organization at all. Every foreign
key in this schema is created DEFERRABLE INITIALLY DEFERRED, so inside one
transaction the rows are deleted table by table with plain SQL, in any order,
and Postgres checks every reference once, at commit. A reference that would
dangle -- a row outside the organization pointing in -- fails the commit and
rolls the entire purge back, which is the failure mode wanted: all or nothing.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from core.middleware import acting_as

logger = logging.getLogger("hrms.platform")

#: Cancelled organizations may export for this long; archive waits for it.
ARCHIVE_AFTER_CANCEL_DAYS = 90
#: How long an archived organization is kept before it may be purged.
PURGE_AFTER_ARCHIVE_DAYS = 365


class LifecycleError(Exception):
    """A lifecycle step refused before anything was written."""


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive_organization(organization, *, actor, reason: str):
    """
    CANCELLED -> ARCHIVED, once the export window has closed.

    Refused for anything but a cancelled organization, and refused inside the
    window: archiving early would take away the export the customer was
    promised after cancellation. A reason is required, because "why was this
    customer archived" is asked long after anyone remembers.
    """
    from apps.audit.events import record_event
    from apps.organization.models import Organization, OrgStatus
    from apps.platform.models import Subscription

    reason = (reason or "").strip()
    if not reason:
        raise LifecycleError("Archiving an organization needs a reason.")

    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(pk=organization.pk)
        if organization.status != OrgStatus.CANCELLED:
            raise LifecycleError(
                f"{organization.slug} is {organization.get_status_display()}. "
                f"Only a cancelled organization can be archived."
            )
        cancelled_at = (
            Subscription.objects.filter(organization=organization, is_active=True)
            .values_list("cancelled_at", flat=True)
            .first()
        )
        if cancelled_at is not None:
            opens = cancelled_at + timedelta(days=ARCHIVE_AFTER_CANCEL_DAYS)
            if timezone.now() < opens:
                raise LifecycleError(
                    f"{organization.slug}'s export window is open until "
                    f"{opens:%d %b %Y}. Archiving now would take away the "
                    f"export its customer was promised."
                )

        organization.status = OrgStatus.ARCHIVED
        organization.archived_at = timezone.now()
        organization.save(update_fields=["status", "archived_at", "updated_at"])

        with acting_as(actor, organization=organization):
            record_event(
                organization,
                actor=actor,
                entity_type="organization.Organization",
                verb="update",
                resource="",
                before={"status": OrgStatus.CANCELLED},
                after={"status": OrgStatus.ARCHIVED, "event": "archived"},
                reason=reason,
            )
    return organization


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


@dataclass
class PurgePlan:
    """What a purge would do. Built without writing, so `--dry-run` is honest."""

    organization: object
    rows: dict[str, int] = field(default_factory=dict)
    users_deleted: list[str] = field(default_factory=list)
    users_spared: list[str] = field(default_factory=list)
    audit_rows: int = 0
    media_path: Path | None = None

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def _organization_tables():
    """
    Every table whose rows belong to exactly one organization and go with it.

    The 94 organization-owned models, found from the registry rather than
    listed -- a model added next year is purged with the rest, not left behind
    as an orphan -- plus the three organization-level rows that are not
    tenant-scoped models but are this organization's all the same.
    """
    from django.apps import apps

    from apps.organization.models import (
        OrganizationMembership,
        OrgEmailConfig,
        OrgSettings,
    )
    from core.models import OrgOwnedModel, OrgOwnedTimestampedModel

    owned = [
        model
        for model in apps.get_models()
        if issubclass(model, (OrgOwnedModel, OrgOwnedTimestampedModel))
        and not model._meta.proxy
    ]
    return owned + [OrgEmailConfig, OrgSettings, OrganizationMembership]


def _count(model, organization) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {connection.ops.quote_name(model._meta.db_table)} "
            f"WHERE organization_id = %s",
            [organization.pk],
        )
        return cursor.fetchone()[0]


def _media_path(organization) -> Path:
    return Path(settings.MEDIA_ROOT) / "organizations" / str(organization.pk)


def _customer_users(organization):
    """
    (to delete, to spare): this organization's logins.

    A platform operator is never a customer's user, whatever membership rows
    might say. A login that submitted or verified a deployment-wide statutory
    rate set is spared -- see the module docstring.
    """
    from django.db.models import Q

    from apps.accounts.models import User
    from apps.organization.models import OrganizationMembership
    from apps.statutory.models import StatutoryRuleSet

    member_ids = set(
        OrganizationMembership.objects.filter(organization=organization).values_list(
            "user_id", flat=True
        )
    )
    # Users who ALSO belong elsewhere keep their login. V1 forbids it; the
    # schema allows it, and a purge is the wrong place to discover it.
    elsewhere = set(
        OrganizationMembership.objects.filter(user_id__in=member_ids)
        .exclude(organization=organization)
        .values_list("user_id", flat=True)
    )
    candidates = User.objects.filter(pk__in=member_ids - elsewhere, is_platform_admin=False)
    certifiers = set(
        StatutoryRuleSet.objects.filter(
            Q(submitted_by__in=candidates) | Q(verified_by__in=candidates)
        ).values_list("submitted_by", "verified_by")
    )
    certifier_ids = {uid for pair in certifiers for uid in pair if uid}
    spared = list(candidates.filter(pk__in=certifier_ids))
    deleted = list(candidates.exclude(pk__in=certifier_ids))
    return deleted, spared


def plan_purge(organization) -> PurgePlan:
    """Everything a purge would remove, counted, with nothing written."""
    from apps.audit.models import AuditLog

    deleted, spared = _customer_users(organization)
    plan = PurgePlan(
        organization=organization,
        users_deleted=sorted(user.email for user in deleted),
        users_spared=sorted(user.email for user in spared),
        audit_rows=AuditLog.objects.filter(organization=organization).count(),
        media_path=_media_path(organization),
    )
    for model in _organization_tables():
        count = _count(model, organization)
        if count:
            plan.rows[model._meta.label] = count
    return plan


def purge_refusal(organization) -> str | None:
    from apps.organization.models import OrgStatus

    if organization.purged_at is not None:
        return f"{organization.slug} was already purged on {organization.purged_at:%d %b %Y}."
    if organization.status != OrgStatus.ARCHIVED or organization.archived_at is None:
        return (
            f"{organization.slug} is {organization.get_status_display()}. Only an "
            f"archived organization can be purged; archive it first."
        )
    eligible = organization.archived_at + timedelta(days=PURGE_AFTER_ARCHIVE_DAYS)
    if timezone.now() < eligible:
        return (
            f"{organization.slug} was archived on {organization.archived_at:%d %b %Y} "
            f"and may not be purged before {eligible:%d %b %Y} "
            f"({PURGE_AFTER_ARCHIVE_DAYS} days)."
        )
    return None


def purge_organization(organization, *, confirm: str, actor=None) -> PurgePlan:
    """
    Remove an archived organization's data. Irreversible; everything or nothing.

    `confirm` must equal the organization's slug -- typed by a person, not
    passed through from a list. The eligibility rules are re-checked INSIDE
    the transaction on a locked row, so two operators racing the same purge
    produce one purge and one refusal.
    """
    from apps.accounts.models import User
    from apps.audit.events import record_event
    from apps.audit.purge import scrub_for_purge
    from apps.organization.models import Organization

    if confirm != organization.slug:
        raise LifecycleError(
            f"Confirmation {confirm!r} does not match the organization's slug "
            f"{organization.slug!r}. Nothing was purged."
        )

    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(pk=organization.pk)
        refusal = purge_refusal(organization)
        if refusal:
            raise LifecycleError(refusal)

        plan = plan_purge(organization)
        deleted, spared = _customer_users(organization)
        customer_ids = [user.pk for user in deleted + spared]

        # 1. The trail loses its personal content before the rows it describes
        #    go, so nothing in it points at a deleted employee at commit.
        plan.audit_rows = scrub_for_purge(organization, customer_user_ids=customer_ids)

        # 2. The organization's rows. Plain SQL, any order; the deferred
        #    constraints are checked once, at commit.
        with connection.cursor() as cursor:
            for model in _organization_tables():
                cursor.execute(
                    f"DELETE FROM {connection.ops.quote_name(model._meta.db_table)} "
                    f"WHERE organization_id = %s",
                    [organization.pk],
                )

        # 3. The logins. Through the ORM, whose collector clears the tokens and
        #    admin log entries that point at them; the rows that PROTECT them
        #    were deleted in step 2, inside this same transaction.
        User.objects.filter(pk__in=[user.pk for user in deleted]).delete()
        for user in spared:
            user.is_active = False
            user.set_unusable_password()
            user.save(update_fields=["is_active", "password"])

        # 4. The tombstone, and the one audit row whose payload survives: it
        #    holds counts, not people.
        organization.purged_at = timezone.now()
        organization.save(update_fields=["purged_at", "updated_at"])
        with acting_as(actor, organization=organization):
            record_event(
                organization,
                actor=actor,
                entity_type="organization.Organization",
                verb="delete",
                resource="",
                after={
                    "event": "organization_purged",
                    "rows_deleted": plan.rows,
                    "users_deleted": len(plan.users_deleted),
                    "users_spared": len(plan.users_spared),
                    "audit_rows_scrubbed": plan.audit_rows,
                },
            )

        # 5. Files, after commit only: a rolled-back purge must not have taken
        #    the files with it.
        media = plan.media_path

        def _remove_files():
            if media is not None and media.exists():
                shutil.rmtree(media, ignore_errors=False)
                logger.info("platform.purge_files org=%s path=%s", organization.slug, media)

        transaction.on_commit(_remove_files)

    logger.warning(
        "platform.organization_purged org=%s rows=%s users=%s",
        organization.slug, plan.total_rows, len(plan.users_deleted),
    )
    return plan
