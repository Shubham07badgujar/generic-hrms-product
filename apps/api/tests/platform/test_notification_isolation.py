"""
No message produced by one organization's action may reach another's people.

This is the notification equivalent of the write-injection matrix, and it
exists because the leak it closes was real. `_users_holding()` resolved the
audience for "someone must approve this payroll run" by reading EVERY active
user on the platform and keeping whichever ones held `PAYROLL_RUN/APPROVE` --
which is precisely what every customer's own finance lead holds. One company
processing payroll therefore addressed every other company's approvers, with
its employee count and net pay in the body, and sent them email.

The matrix below drives the real event functions with one organization's
records while the other organization's people sit in the same database, and
asserts three things of everything produced: the row belongs to the acting
organization, the recipient is a member of it, and no message left the
building addressed to anyone else.

`test_every_fan_out_event_is_in_the_matrix` is the guard on the guard. It
reads `events.py` with the AST, finds every function that resolves an audience
rather than a single named person, and fails if one is not exercised here --
so an event added next year is covered on the day it is added rather than the
day someone remembers.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from django.core import mail

from apps.notifications import events
from apps.notifications.models import Notification, NotificationDelivery
from core.middleware import acting_as

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------- the matrix


def _department_decision(world, other):
    events.department_decision_recorded_for(
        application=world.rows["application"],
        decision="recommend_reject",
        rationale="Not a fit for the role as scoped.",
    )


def _document_uploaded(world, other):
    events.document_uploaded(world.rows["employee_document"])


def _probation_review_due(world, other):
    events.probation_review_due(world.rows["probation_review"])


def _resignation_submitted(world, other):
    events.resignation_submitted(world.rows["resignation"])


def _payroll_processed(world, other):
    events.payroll_processed(world.rows["payroll_run"])


def _payroll_approved(world, other):
    events.payroll_approved(world.rows["payroll_run"])


def _payroll_reversed(world, other):
    events.payroll_reversed(
        world.rows["payroll_run"], reason="Wrong attendance window was used."
    )


def _give_finance_head(world):
    """
    Certifying statutory rates is the Finance Head's alone.

    The shared two-organization fixture builds an admin, an HR lead and a
    plain employee, none of whom hold `STATUTORY_CONFIG/APPROVE`. Without a
    holder on BOTH sides the statutory case proves nothing in either
    direction: the event would address nobody in its own organization, and
    there would be nobody in the other one for the old global audience to have
    leaked to.
    """
    import datetime as dt

    from apps.accounts.models import Role, User, UserRole
    from apps.employees.models import Employee
    from apps.organization.models import MembershipStatus, OrganizationMembership
    from core.access.catalog import RoleCode

    email = f"finance@{world.slug}.example"
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        return existing

    # Built under THIS world's own context, never the caller's. `UserRole` is
    # organization-owned, so creating one for the other organization while A
    # is bound would stamp it A and disagree with the role it points at.
    with acting_as(None, organization=world.organization):
        user = User.objects.create_user(
            email=email, password="test-password-12345", first_name="Finance"
        )
        OrganizationMembership.objects.create(
            organization=world.organization, user=user, status=MembershipStatus.ACTIVE
        )
        role = Role.objects.get(
            organization=world.organization, code=RoleCode.FINANCE_HEAD
        )
        UserRole.objects.create(user=user, role=role)
        # Finance Head is a role that `requires_employee`, and step 7 of
        # context resolution empties the grants of a principal that needs an
        # employee record and has none. Without this the user holds the role
        # and resolves to no scope at all.
        Employee.objects.create(
            employee_code=f"FIN{str(world.organization.pk)[:4]}",
            user=user,
            first_name="Finance",
            last_name="Head",
            department=world.department,
            designation=world.designation,
            location=world.location,
            level=world.level,
            date_of_joining=dt.date(2024, 1, 1),
        )
    return user


def _statutory_verification_due(world, other):
    from apps.statutory.models import StatutoryRuleSet

    _give_finance_head(world)
    _give_finance_head(other)

    rule_sets = list(StatutoryRuleSet.objects.all()[:1]) or ["placeholder"]
    events.statutory_verification_due(
        rule_sets, organization=world.organization.pk
    )


def _admin_override(world, other):
    from apps.recruitment.models import DecisionOverride

    override = DecisionOverride.objects.create(
        application=world.rows["application"],
        overridden_by=world.admin,
        previous_status="rejected",
        new_status="active",
        reason="Reopened after the hiring manager withdrew the objection.",
    )
    events.admin_override(override, subject_label=override.application.candidate.full_name)


#: Every event whose audience is resolved rather than named. Keyed by the
#: function in `events.py` it exercises, which is what the completeness guard
#: below matches against.
FAN_OUT_EVENTS = {
    "department_decision_recorded_for": _department_decision,
    "document_uploaded": _document_uploaded,
    "probation_review_due": _probation_review_due,
    "resignation_submitted": _resignation_submitted,
    "payroll_processed": _payroll_processed,
    "payroll_approved": _payroll_approved,
    "payroll_reversed": _payroll_reversed,
    "statutory_verification_due": _statutory_verification_due,
    "admin_override": _admin_override,
}


def _members_of(world) -> set:
    from apps.organization.membership import member_users

    return {user.pk for user in member_users(world.organization)}


def _emails_of(world) -> set:
    from apps.organization.membership import member_users

    return {user.email.lower() for user in member_users(world.organization)}


@pytest.mark.parametrize("event_name", sorted(FAN_OUT_EVENTS))
def test_an_event_in_one_organization_addresses_nobody_in_the_other(
    event_name, org_a, org_b
):
    """
    The whole point of the slice, one event at a time.

    Both organizations exist, both have an admin and an HR lead holding the
    same permissions under the same role codes, and the event fires for A.
    Nothing it produces may name anyone in B.
    """
    mail.outbox.clear()
    with acting_as(org_a.admin, organization=org_a.organization):
        FAN_OUT_EVENTS[event_name](org_a, org_b)

    # Membership is read AFTER the event, not before. A case that adds a
    # principal to the other organization in order to have someone to leak to
    # would otherwise be measured against a roster taken before that principal
    # existed, and would pass while leaking to them.
    a_members, b_members = _members_of(org_a), _members_of(org_b)
    assert a_members and b_members, "both organizations need people for this to prove anything"
    assert not (a_members & b_members), "the fixtures must not share users"

    from tests.conftest import across_organizations

    with across_organizations():
        produced = list(Notification.objects.all_orgs().filter(recipient_id__in=b_members))
    assert produced == [], (
        f"{event_name} in {org_a.slug} addressed {len(produced)} "
        f"notification(s) to members of {org_b.slug}"
    )

    strayed = [
        message
        for message in mail.outbox
        if {address.lower() for address in message.to} & _emails_of(org_b)
    ]
    assert strayed == [], (
        f"{event_name} in {org_a.slug} emailed {len(strayed)} message(s) "
        f"to members of {org_b.slug}"
    )


@pytest.mark.parametrize("event_name", sorted(FAN_OUT_EVENTS))
def test_every_row_an_event_writes_belongs_to_the_acting_organization(
    event_name, org_a, org_b
):
    """
    The other half: rows land in the right tenant, not merely away from the wrong one.

    A notification addressed correctly but stamped with no organization would
    pass the test above and still be invisible to the person it was written
    for, so the stamping is asserted separately.
    """
    with acting_as(org_a.admin, organization=org_a.organization):
        FAN_OUT_EVENTS[event_name](org_a, org_b)

    from tests.conftest import across_organizations

    a_members = _members_of(org_a)
    with across_organizations():
        written = list(Notification.objects.all_orgs().all())
        deliveries = list(
            NotificationDelivery.objects.all_orgs().select_related("notification")
        )

    # The positive control, and the reason this assertion is here rather than
    # in a separate test: without it every case below would also pass against
    # an event that had stopped notifying anybody at all, which is exactly the
    # failure an over-tightened audience filter produces.
    assert written, (
        f"{event_name} notified nobody in its own organization — this case "
        f"proves nothing about isolation until it does"
    )

    for notification in written:
        assert notification.organization_id == org_a.organization.pk
        assert notification.recipient_id in a_members

    for delivery in deliveries:
        assert delivery.organization_id == delivery.notification.organization_id


# --------------------------------------------------- the guard on the guard


def _fan_out_functions_in_events_module() -> set[str]:
    """
    Every top-level function in `events.py` that resolves an audience.

    Read from the source rather than a maintained list, for the same reason
    the route walker derives its routes from the resolver: a list someone has
    to remember to append to is a list that stops being true.
    """
    source = pathlib.Path(events.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_users_holding"
            ):
                found.add(node.name)
                break
    return found


def _every_audience_call() -> list[tuple[str, int, int]]:
    """
    Every call to `_users_holding` anywhere under `apps/`, with its arity.

    Deliberately not limited to `events.py`. The first version of this guard
    was, and it therefore said nothing about the two call sites in
    `apps/leave/services.py` and `apps/recruitment/services/slots.py` — both
    of which resolved an audience across every organization on the platform
    exactly as the events did. A guard that only inspects the module where the
    helper is DEFINED misses every caller that imports it, which is the
    interesting half.
    """
    root = pathlib.Path(events.__file__).resolve().parents[2]

    calls = []
    for path in (root / "apps").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a file we cannot parse
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_users_holding"
            ):
                relative = path.relative_to(root).as_posix()
                calls.append((relative, node.lineno, len(node.args)))
    return calls


def test_no_audience_is_resolved_without_naming_an_organization():
    """
    The mechanical version of the rule, checked everywhere rather than in the
    one module that happens to define the helper.

    Three positional arguments is the whole signature: organization, resource,
    action. A call with two is a call that named a permission and not a tenant,
    and that is the defect this slice exists to close — in any file, including
    one written next year by someone who imported the helper without reading
    it.
    """
    calls = _every_audience_call()

    assert len(calls) >= 11, (
        f"the scan found only {len(calls)} audience call(s), which means it "
        f"stopped matching the codebase rather than that the codebase stopped "
        f"resolving audiences"
    )

    understaffed = [
        f"{path}:{line} ({count} positional arguments)"
        for path, line, count in calls
        if count < 3
    ]
    assert not understaffed, (
        "these resolve an audience without naming an organization, so they "
        "address every customer who holds the permission: "
        + ", ".join(understaffed)
    )


def test_every_fan_out_event_is_in_the_matrix():
    declared = set(FAN_OUT_EVENTS)
    actual = _fan_out_functions_in_events_module()

    assert actual, (
        "the AST scan found no fan-out events at all, which means it stopped "
        "matching the module rather than that the module stopped fanning out"
    )
    missing = actual - declared
    assert not missing, (
        f"these events resolve an audience but are not exercised by the "
        f"isolation matrix: {sorted(missing)}"
    )
    stale = declared - actual
    assert not stale, f"these matrix entries no longer fan out: {sorted(stale)}"


def test_the_audience_resolver_requires_an_organization():
    """
    `_users_holding` must not be callable without naming a tenant.

    A keyword argument with a `None` default would have kept every existing
    call site working and left the leak in place; a required positional is
    what makes "whose?" unanswerable-by-omission.
    """
    import inspect

    parameters = list(inspect.signature(events._users_holding).parameters.values())
    first = parameters[0]
    assert first.name == "organization"
    assert first.default is inspect.Parameter.empty
    assert first.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def test_an_unnamed_organization_addresses_nobody(org_a, org_b):
    """`None` must mean nobody, not everybody. The fail-closed half."""
    from core.access import Action, Resource

    assert events._users_holding(None, Resource.PAYROLL_RUN, Action.APPROVE) == []


def test_the_resolver_finds_the_right_people_when_it_is_given_an_organization(org_a):
    """
    A positive control.

    Without it, every assertion above would also pass against a resolver that
    had simply stopped returning anyone.
    """
    from core.access import Action, Resource

    found = events._users_holding(
        org_a.organization, Resource.PAYROLL_RUN, Action.APPROVE
    )
    assert found, "no one in this organization can approve payroll — the fixture is wrong"
    assert {user.pk for user in found} <= _members_of(org_a)


# ------------------------------------------------ the chokepoint in notify()


def test_notify_refuses_a_recipient_from_another_organization(org_a, org_b, caplog):
    """
    Even a caller that resolves its audience wrongly cannot deliver.

    `_users_holding` is the first layer and this is the second. A future event
    that builds a recipient list by hand -- and several already do, from a
    reporting manager or a job opening's recruiter -- is covered by this one
    without having to remember anything.
    """
    from apps.notifications.services import notify

    with acting_as(org_a.admin, organization=org_a.organization):
        result = notify(
            recipient=org_b.admin,
            kind="payroll_processed",
            title="Payroll is ready for review",
        )

    assert result is None
    assert Notification.objects.all_orgs().count() == 0
    assert "cross_tenant_recipient_refused" in caplog.text


def test_notify_refuses_when_no_organization_is_bound(org_a, caplog):
    """
    An unbound caller addresses nobody.

    This is the Celery shape: a task that did not bind its tenant has no way
    to show that a recipient is in scope, and writing the row anyway would
    stamp it from whatever the worker last did.
    """
    from apps.notifications.services import notify

    with acting_as(None, organization=None):
        result = notify(
            recipient=org_a.admin,
            kind="payroll_processed",
            title="Payroll is ready for review",
        )

    assert result is None
    assert Notification.objects.all_orgs().count() == 0
    assert "unbound_organization" in caplog.text


def test_notify_still_delivers_to_a_member(org_a):
    """The positive control for both refusals above."""
    from apps.notifications.services import notify

    with acting_as(org_a.admin, organization=org_a.organization):
        result = notify(
            recipient=org_a.admin,
            kind="payroll_processed",
            title="Payroll is ready for review",
        )

    assert result is not None
    assert result.organization_id == org_a.organization.pk
    assert result.recipient_id == org_a.admin.pk
