"""
Subscription transitions, and the seat check.

THE ONE WRITE PATH TO `Organization.status`

`Subscription.status` is commercial state and `Organization.status` is
authoritative for access. Keeping them from diverging is not a matter of
remembering: every commercial transition goes through `_apply_to_organization`
below, which is the only place in this app that writes the organization's
status. A second writer is how the two fields start disagreeing, and a customer
starts being locked out for a reason nobody can find.

WHAT A DOWNGRADE DOES NOT DO

It does not delete anything. Every PayrollRun, Payslip, SalaryStructure and
statutory record of an organization that leaves a payroll-enabled plan remains
stored, intact and unmodified. The module's WRITES stop; reads and exports keep
working for a defined window, because a customer must be able to retrieve
records they are statutorily obliged to keep. That window is enforced by
`FeatureEnabled`, and it starts at `features_narrowed_at`, which this module
stamps.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.api.exceptions import BusinessRuleError

logger = logging.getLogger("hrms.platform")


class SubscriptionError(Exception):
    """A refusal the operator can act on."""


class SeatLimitReached(BusinessRuleError):
    """
    The organization is at its plan's seat limit.

    A `BusinessRuleError` -- 422 -- and not a 400 or a 403. Nobody typed
    anything wrong, so it is not a validation error; and the caller is fully
    entitled to hire, so it is not a permission denial. The system will not
    allow it YET, which is exactly what 422 means here and what the SPA already
    renders differently.

    Its own subclass, with its own code, so the client can offer an upgrade
    rather than the generic "not permitted by a business rule" -- running out
    of seats is the one business rule with an obvious next action.
    """

    default_code = "seat_limit_reached"

    def __init__(self, *, limit: int, current: int, requested: int = 1):
        self.limit = limit
        self.current = current
        self.requested = requested
        super().__init__(
            f"This plan allows {limit} active employees and the organization "
            f"has {current}."
            + (
                f" This would add {requested}."
                if requested > 1
                else ""
            )
        )


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------


def active_employee_count(organization) -> int:
    from apps.employees.models import Employee
    from core.models import org_scoped

    return org_scoped(Employee, organization).filter(is_active=True).count()


def _organization_id(organization):
    """Accept an Organization or its id, so callers use whichever they hold."""
    return getattr(organization, "pk", organization)


@transaction.atomic
def seats_remaining(organization) -> int | None:
    """
    How many more employees this organization may have, or None for unlimited.

    The read-only counterpart to `reserve_seats`, and deliberately NOT a second
    opinion: both ask `active_employee_count` against the same limit. It takes
    no lock, because nothing is being decided -- this is the number shown to a
    person before they act, and by the time they do another hire may have
    happened. The decision is `reserve_seats`, under a row lock, at the moment
    of the write.

    None for an organization with no subscription, matching `reserve_seats`
    treating that as unlimited: a self-hosted deployment never bought seats.
    """
    from apps.platform.models import Subscription

    subscription = (
        Subscription.objects.filter(
            organization_id=_organization_id(organization), is_active=True
        )
        .select_related("plan")
        .first()
    )
    if subscription is None or subscription.employee_limit is None:
        return None
    used = active_employee_count(_organization_id(organization))
    return max(subscription.employee_limit - used, 0)


def reserve_seats(organization, *, count: int = 1):
    """
    Refuse if adding `count` employees would exceed the plan's seat limit.

    LOCKS THE SUBSCRIPTION ROW, and that is the whole reason this is a service
    rather than a `count()` at the call site. A bare count is a TOCTOU hole:
    two concurrent hires both read limit-1, both decide there is room, and both
    commit. The lock serialises the decision, and because the caller is already
    inside a transaction the lock is held until the employee exists.

    An organization with no subscription is UNLIMITED, not refused. Self-hosted
    single-company deployments have no plans at all, and a seat check that
    bricked them would be enforcing a SaaS concern on an installation that
    never bought one.
    """
    from apps.platform.models import Subscription

    # `is_active=True` matters: `Subscription` is a BaseModel, so `.delete()`
    # is a SOFT delete. Without this filter a deleted subscription still
    # enforced its seat limit while `_plan_state` -- which does filter --
    # granted the organization every feature. A row that is gone for one
    # question and present for another is the kind of disagreement nobody
    # finds until a customer is stuck.
    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization_id=_organization_id(organization), is_active=True)
        .select_related("plan")
        .first()
    )
    if subscription is None:
        return None

    limit = subscription.employee_limit
    if limit is None:
        return subscription

    current = active_employee_count(_organization_id(organization))
    if current + count > limit:
        raise SeatLimitReached(limit=limit, current=current, requested=count)
    return subscription


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

#: Commercial state -> the organization status it implies, or None to leave the
#: organization alone.
#:
#: PAST_DUE maps to None deliberately. Locking an HR department out of payroll
#: on the 30th because an invoice is late punishes the employees rather than the
#: buyer; the warning belongs in the snapshot, and the conversation happens out
#: of band.
_ORG_STATUS_FOR = {
    "trialing": "trial",
    "active": "active",
    "past_due": None,
    "cancelled": "cancelled",
    "expired": "cancelled",
}


def status_after_setup(organization) -> str:
    """
    The organization status that finishing setup lands on.

    Asked by `finish_setup`, which used to answer it itself with a hard-coded
    ACTIVE. That made it a second writer of `Organization.status` with its own
    opinion, and the two disagreed: a customer who finished setup during their
    trial read ACTIVE while their subscription said `trialing`. The mapping
    from commercial state to access state lives here and only here, so setup
    asks it instead of repeating it.

      trialing            -> TRIAL
      active              -> ACTIVE
      past_due            -> ACTIVE   (it maps to "leave alone", and "alone"
                                       for a company leaving setup is live)
      no subscription     -> ACTIVE   (a self-hosted deployment sells nothing)

    Cancelled and expired cannot reach this: `_apply_to_organization` moves a
    pending organization to CANCELLED the moment its subscription goes there,
    and `finish_setup` refuses anything not in PENDING_SETUP.
    """
    from apps.organization.models import OrgStatus
    from apps.platform.models import Subscription

    subscription = (
        Subscription.objects.filter(organization=organization, is_active=True)
        .only("status")
        .first()
    )
    if subscription is None:
        return OrgStatus.ACTIVE
    implied = _ORG_STATUS_FOR.get(str(subscription.status))
    return OrgStatus.TRIAL if implied == OrgStatus.TRIAL else OrgStatus.ACTIVE


def _apply_to_organization(subscription, *, actor=None):
    """
    Write the organization status a subscription implies.

    One of exactly two writers of `Organization.status`. The other is
    `finish_setup`, which takes its answer from `status_after_setup` above --
    so the mapping itself exists once, whichever of the two writes it.
    """
    from apps.organization.models import OrgStatus

    implied = _ORG_STATUS_FOR.get(str(subscription.status))
    if implied is None:
        return None

    organization = subscription.organization
    # An organization still walking the setup wizard stays in PENDING_SETUP:
    # it is a working state the administrator is in the middle of, and a
    # subscription going ACTIVE underneath them must not skip it.
    if organization.status == OrgStatus.PENDING_SETUP and implied in (
        OrgStatus.ACTIVE,
        OrgStatus.TRIAL,
    ):
        return None
    if organization.status == implied:
        return None

    before = organization.status
    organization.status = implied
    organization.save(update_fields=["status", "updated_at"])
    logger.info(
        "platform.org_status org=%s %s -> %s (subscription %s)",
        organization.slug, before, implied, subscription.status,
    )
    return before


@transaction.atomic
def start_subscription(organization, *, plan, actor=None, trial_days: int | None = 14):
    """The trial a newly provisioned organization starts on."""
    from apps.audit.events import record_event
    from apps.platform.models import Subscription, SubscriptionStatus
    from core.middleware import acting_as

    if Subscription.objects.filter(
        organization=organization, is_active=True
    ).exists():
        raise SubscriptionError(
            f"{organization.slug} already has a subscription. Change the plan "
            f"instead of starting a second one."
        )

    subscription = Subscription.objects.create(
        organization=organization,
        plan=plan,
        status=SubscriptionStatus.TRIALING,
        ends_at=(
            timezone.now() + timezone.timedelta(days=trial_days)
            if trial_days
            else None
        ),
    )
    with acting_as(actor, organization=organization):
        record_event(
            subscription,
            actor=actor,
            entity_type="platform.Subscription",
            verb="create",
            resource="",
            after={"plan": plan.code, "status": subscription.status},
        )
    return subscription


@transaction.atomic
def change_plan(organization, *, plan, actor=None, reason: str = "", force: bool = False):
    """
    Move a customer to a different plan.

    A DOWNGRADE BELOW THE SEAT LIMIT IS REFUSED AT THE POINT OF CHANGE, with
    the numbers in the message, rather than accepted and then enforced by
    breaking the customer's next hire. `force` exists because a platform admin
    sometimes has an agreement the system does not know about -- it requires a
    reason, and the reason is audited.

    Narrowing features stamps `features_narrowed_at`, which starts the
    read-and-export grace window. Widening does not: gaining a module should
    never shorten the window protecting a different one.
    """
    from apps.audit.events import record_event
    from apps.platform.models import Subscription
    from core.middleware import acting_as

    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization=organization, is_active=True)
        .select_related("plan")
        .first()
    )
    if subscription is None:
        raise SubscriptionError(f"{organization.slug} has no subscription.")

    previous = subscription.plan
    if previous.pk == plan.pk:
        return subscription

    limit = plan.employee_limit
    if limit is not None:
        current = active_employee_count(organization)
        if current > limit and not force:
            raise SubscriptionError(
                f"{plan.name} allows {limit} active employees and "
                f"{organization.name} has {current}. Reduce the headcount "
                f"first, or override with a reason."
            )
    if force and not reason.strip():
        raise SubscriptionError("An override needs a reason.")

    lost = sorted(set(previous.enabled_features) - set(plan.enabled_features))
    subscription.plan = plan
    fields = ["plan", "updated_at"]
    if lost:
        subscription.features_narrowed_at = timezone.now()
        fields.append("features_narrowed_at")
    subscription.save(update_fields=fields)

    with acting_as(actor, organization=organization):
        record_event(
            subscription,
            actor=actor,
            entity_type="platform.Subscription",
            verb="update",
            resource="",
            before={"plan": previous.code},
            after={
                "plan": plan.code,
                "features_lost": lost,
                "forced": bool(force),
            },
            reason=reason or None,
        )
    return subscription


@transaction.atomic
def set_status(organization, *, status, actor=None, reason: str = ""):
    """
    Move the commercial state, and let it write the access state.

    One function rather than `suspend()`, `cancel()`, `reactivate()`: the
    interesting part is the mapping, and three wrappers around one mapping is
    three places for it to drift.
    """
    from apps.audit.events import record_event
    from apps.platform.models import Subscription, SubscriptionStatus
    from core.middleware import acting_as

    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization=organization, is_active=True)
        .select_related("plan", "organization")
        .first()
    )
    if subscription is None:
        raise SubscriptionError(f"{organization.slug} has no subscription.")

    status = str(status)
    if status not in {str(s) for s in SubscriptionStatus}:
        raise SubscriptionError(f"{status!r} is not a subscription status.")

    before = subscription.status
    if before == status:
        return subscription

    subscription.status = status
    fields = ["status", "updated_at"]
    if status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED):
        subscription.cancelled_at = timezone.now()
        fields.append("cancelled_at")
    subscription.save(update_fields=fields)

    org_before = _apply_to_organization(subscription, actor=actor)

    with acting_as(actor, organization=organization):
        record_event(
            subscription,
            actor=actor,
            entity_type="platform.Subscription",
            verb="update",
            resource="",
            before={"status": before, "organization_status": org_before},
            after={
                "status": status,
                "organization_status": subscription.organization.status,
            },
            reason=reason or None,
        )
    return subscription
