"""
Plans, subscriptions, and the two properties that make feature gating safe.

The first is TOTALITY: every `Resource` maps to exactly one feature, checked at
build time. Without it a new module ships ungated by omission, which nobody
notices because the symptom is a customer getting something for free.

The second is the STATUS SPLIT: `Organization.status` is authoritative for
access and `Subscription.status` is commercial, and exactly one code path lets
the second write the first. Two fields answering "is this customer live?" is
how they diverge, and a customer locked out for a reason nobody can find is the
usual result.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.platform.models import Plan, Subscription, SubscriptionStatus
from apps.platform.services.provisioning import provision_organization
from apps.platform.services.subscriptions import (
    SeatLimitReached,
    SubscriptionError,
    change_plan,
    reserve_seats,
    set_status,
    start_subscription,
)
from core.access.catalog import Resource
from core.access.features import ALWAYS_ON, FEATURE_OF_RESOURCE, FeatureCode

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)
    return {p.code: p for p in Plan.objects.all()}


def _provision(plans=None, plan=None, **overrides):
    kwargs = {
        "name": "Northwind Health",
        "slug": "northwind",
        "admin_email": "admin@northwind.example",
    }
    kwargs.update(overrides)
    return provision_organization(plan=plan, **kwargs)


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------


def test_every_resource_maps_to_exactly_one_feature():
    """
    The property the whole gating design rests on, asserted here as well as in
    `manage.py check` -- because a check that stops being registered reports
    success forever, and this file would still fail.
    """
    missing = {str(r) for r in Resource} - {str(k) for k in FEATURE_OF_RESOURCE}
    assert not missing, (
        f"These resources are governed by no feature, so every plan gets them "
        f"free: {sorted(missing)}"
    )

    unknown = {str(f) for f in FEATURE_OF_RESOURCE.values()} - {
        str(f) for f in FeatureCode
    }
    assert not unknown, f"Mapped to features that do not exist: {sorted(unknown)}"


def test_the_build_check_actually_bites():
    """
    Guards the guard. A check reduced to a no-op passes everything, so this
    calls it with the map temporarily broken and asserts it complains.
    """
    from core.access import checks, features

    original = features.FEATURE_OF_RESOURCE
    try:
        features.FEATURE_OF_RESOURCE = {
            k: v for k, v in original.items() if k != Resource.PAYROLL_RUN
        }
        errors = checks.check_every_resource_has_a_feature(None)
    finally:
        features.FEATURE_OF_RESOURCE = original

    assert {e.id for e in errors} == {"access.E014"}, errors


def test_core_is_never_disableable(plans):
    """
    An HRMS without employees is not a cheaper HRMS, it is a broken one. A
    plan that could disable CORE would sell one.
    """
    from django.core.exceptions import ValidationError

    plan = Plan(code="broken", name="Broken", disabled_features=[FeatureCode.CORE])
    with pytest.raises(ValidationError, match="cannot be disabled"):
        plan.clean()

    for plan in plans.values():
        for feature in ALWAYS_ON:
            assert plan.includes(feature)


def test_a_plan_rejects_features_that_do_not_exist():
    from django.core.exceptions import ValidationError

    plan = Plan(code="typo", name="Typo", disabled_features=["payrol"])
    with pytest.raises(ValidationError, match="Not features"):
        plan.clean()


def test_absence_means_included(plans):
    """
    The opposite convention from the permission grants map, deliberately. If
    absence meant "disabled", adding a FeatureCode would silently switch a
    module off for every existing customer at deploy time.
    """
    enterprise = plans["enterprise"]
    assert enterprise.disabled_features == []
    assert set(enterprise.enabled_features) == {str(f) for f in FeatureCode}

    starter = plans["starter"]
    assert not starter.includes(FeatureCode.PAYROLL)
    assert starter.includes(FeatureCode.LEAVE)


# ---------------------------------------------------------------------------
# Provisioning starts a trial
# ---------------------------------------------------------------------------


def test_provisioning_starts_a_trial_on_the_cheapest_public_plan(plans):
    result = _provision()
    assert result.subscription is not None
    assert result.subscription.plan.code == "starter"
    assert result.subscription.status == SubscriptionStatus.TRIALING


def test_provisioning_works_on_a_deployment_that_sells_nothing():
    """
    A self-hosted single-company installation has no plans at all, and a
    provisioning flow that required one would be enforcing a SaaS concern on
    an installation that never bought one.
    """
    assert not Plan.objects.exists()
    result = _provision()
    assert result.subscription is None
    assert result.organization.pk


def test_a_second_subscription_is_refused(plans):
    result = _provision()
    with pytest.raises(SubscriptionError, match="already has a subscription"):
        start_subscription(result.organization, plan=plans["growth"])


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------


def _hire(organization, count):
    import datetime as dt

    from apps.employees.models import Employee
    from apps.organization.models import Department, Designation, EmployeeLevel, Location
    from core.access.catalog import DepartmentKind, Layer
    from core.middleware import acting_as

    with acting_as(None, organization=organization):
        department = Department.objects.create(
            name="People", code="HR", kind=DepartmentKind.HR
        )
        location = Location.objects.create(name="HQ", code="HO")
        designation = Designation.objects.create(title="Officer", department=None)
        level = EmployeeLevel.objects.create(name="Staff", code="L5", layer=Layer.STAFF)
        for i in range(count):
            Employee.objects.create(
                employee_code=f"E{i:04d}",
                first_name="Test",
                last_name=f"Person{i}",
                department=department,
                designation=designation,
                location=location,
                level=level,
                date_of_joining=dt.date(2024, 1, 1),
            )


def test_a_seat_limit_is_enforced(plans):
    result = _provision()  # starter: 25 seats
    _hire(result.organization, 25)

    with pytest.raises(SeatLimitReached) as excinfo:
        reserve_seats(result.organization)

    assert excinfo.value.limit == 25
    assert excinfo.value.current == 25
    assert "25 active employees" in str(excinfo.value)


def test_a_batch_is_checked_as_a_batch(plans):
    """
    A partially imported staff list is worse than a refused one, so the import
    asks about the whole batch rather than discovering the limit on row 24.
    """
    result = _provision()
    _hire(result.organization, 20)

    with pytest.raises(SeatLimitReached):
        reserve_seats(result.organization, count=10)

    # And the same call succeeds within the limit.
    assert reserve_seats(result.organization, count=5) is not None


def test_an_unlimited_plan_has_no_limit(plans):
    result = _provision(plan=plans["enterprise"])
    _hire(result.organization, 30)
    assert reserve_seats(result.organization, count=1000) is not None


def test_no_subscription_means_unlimited():
    """
    Not "refused". A seat check that bricked a self-hosted installation would
    be enforcing something it never bought.
    """
    result = _provision()
    assert result.subscription is None
    _hire(result.organization, 5)
    assert reserve_seats(result.organization, count=10_000) is None


# ---------------------------------------------------------------------------
# Plan changes
# ---------------------------------------------------------------------------


def test_a_downgrade_below_the_headcount_is_refused_with_the_numbers(plans):
    """
    Refused at the point of change, rather than accepted and then enforced by
    breaking the customer's next hire.
    """
    result = _provision(plan=plans["growth"])
    _hire(result.organization, 40)

    with pytest.raises(SubscriptionError, match="allows 25 active employees"):
        change_plan(result.organization, plan=plans["starter"])


def test_an_override_needs_a_reason(plans):
    result = _provision(plan=plans["growth"])
    _hire(result.organization, 40)

    with pytest.raises(SubscriptionError, match="needs a reason"):
        change_plan(result.organization, plan=plans["starter"], force=True)

    changed = change_plan(
        result.organization,
        plan=plans["starter"],
        force=True,
        reason="Migration agreed with the customer until March.",
    )
    assert changed.plan.code == "starter"


def test_a_downgrade_deletes_nothing(plans):
    """
    The SaaS rule that matters most here. Payroll rows of an organization
    leaving a payroll plan stay stored, intact and unmodified -- they are
    exactly the category a customer is statutorily obliged to keep.
    """
    from apps.payroll.models import PayrollRun
    from core.middleware import acting_as
    from core.models import org_scoped

    result = _provision(plan=plans["growth"])
    with acting_as(None, organization=result.organization):
        run = PayrollRun.objects.create(
            period_year=2025, period_month=6, run_by=result.admin
        )

    change_plan(result.organization, plan=plans["starter"])

    run.refresh_from_db()
    assert org_scoped(PayrollRun, result.organization).count() == 1
    assert run.period_year == 2025 and run.period_month == 6


def test_narrowing_features_starts_the_grace_window(plans):
    result = _provision(plan=plans["growth"])
    assert result.subscription.features_narrowed_at is None

    changed = change_plan(result.organization, plan=plans["starter"])
    assert changed.features_narrowed_at is not None


def test_widening_features_does_not_restart_the_window(plans):
    """
    Gaining a module must never shorten the window protecting a different one.
    """
    result = _provision(plan=plans["growth"])
    narrowed = change_plan(result.organization, plan=plans["starter"])
    stamped = narrowed.features_narrowed_at

    widened = change_plan(result.organization, plan=plans["enterprise"])
    assert widened.features_narrowed_at == stamped


# ---------------------------------------------------------------------------
# The status split
# ---------------------------------------------------------------------------


def test_a_commercial_transition_writes_the_access_status(plans):
    from apps.organization.models import OrgStatus

    result = _provision(plan=plans["growth"])
    # Finish setup first: PENDING_SETUP is a working state the administrator
    # is in the middle of, and a subscription must not skip them past it.
    result.organization.status = OrgStatus.ACTIVE
    result.organization.save(update_fields=["status"])

    set_status(result.organization, status=SubscriptionStatus.CANCELLED)

    result.organization.refresh_from_db()
    assert result.organization.status == OrgStatus.CANCELLED


def test_past_due_does_not_lock_anybody_out(plans):
    """
    A product decision, stated in code. Locking an HR department out of payroll
    on the 30th because an invoice is late punishes the employees rather than
    the buyer.
    """
    from apps.organization.models import OrgStatus

    result = _provision(plan=plans["growth"])
    result.organization.status = OrgStatus.ACTIVE
    result.organization.save(update_fields=["status"])

    set_status(result.organization, status=SubscriptionStatus.PAST_DUE)

    result.organization.refresh_from_db()
    assert result.organization.status == OrgStatus.ACTIVE


def test_a_subscription_does_not_skip_an_organization_past_setup(plans):
    from apps.organization.models import OrgStatus

    result = _provision(plan=plans["growth"])
    assert result.organization.status == OrgStatus.PENDING_SETUP

    set_status(result.organization, status=SubscriptionStatus.ACTIVE)

    result.organization.refresh_from_db()
    assert result.organization.status == OrgStatus.PENDING_SETUP


def test_the_status_change_is_audited(plans):
    from apps.audit.models import AuditLog

    result = _provision(plan=plans["growth"])
    set_status(
        result.organization,
        status=SubscriptionStatus.CANCELLED,
        reason="Customer gave notice on 2026-09-01.",
    )

    entry = (
        AuditLog.objects.filter(
            organization=result.organization, entity_type="platform.Subscription"
        )
        .order_by("-occurred_at")
        .first()
    )
    assert entry is not None
    assert entry.after["status"] == "cancelled"
    assert "notice" in entry.reason


def test_an_override_without_a_reason_is_refused_by_the_database(plans):
    """
    Belt and braces: the service requires a reason, and so does a constraint,
    because "why does this customer have 400 seats on a 50-seat plan" has to be
    answerable a year later whatever wrote the row.
    """
    from django.db import IntegrityError, transaction

    result = _provision(plan=plans["growth"])
    subscription = Subscription.objects.get(organization=result.organization)

    with pytest.raises(IntegrityError), transaction.atomic():
        subscription.employee_limit_override = 400
        subscription.override_reason = ""
        subscription.save(update_fields=["employee_limit_override", "override_reason"])
