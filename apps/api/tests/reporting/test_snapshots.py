"""
Metric snapshots are computed and stored per organization.

A snapshot is computed by an unrestricted principal, which bypasses
`scope_queryset()` -- and with it the org predicate the request path applies
before anything else. Every app is on the strict tenant manager now, so the
binding the subtask makes narrows `Employee.objects` as well; the organization
the job passes down is the second of two answers rather than the only one.
These tests exist to fail the day either stops holding, and they are written
against the value rather than against which mechanism produced it.

The two organizations are deliberately given DIFFERENT headcounts: with equal
numbers, a job that summed both tenants and a job that counted one would be
indistinguishable only by row ownership, and a doubled value is the leak that
matters.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.reporting import services
from apps.reporting.models import MetricSnapshot
from apps.reporting.services import refresh_snapshots
from apps.reporting.tasks import refresh_snapshots_for_organization
from core.middleware import acting_as
from django.utils import timezone
from tests.tenancy.conftest import org_a, org_b  # noqa: F401

#: The only snapshotable metric the product ships. Named once so these tests
#: fail loudly rather than vacuously if that stops being true.
TREND = "hr.headcount_trend"

#: A value no real headcount could be, written over the stored rows so that
#: "served from storage" and "recomputed live" are told apart by the number
#: itself rather than by trusting the flag that claims which happened.
IMPOSSIBLE = 4242

pytestmark = pytest.mark.django_db


def _snapshots(world) -> dict[str, float]:
    from tests.conftest import across_organizations

    # Reads one organization's snapshots regardless of what is bound, which is
    # what makes "and none of B's" checkable at all.
    with across_organizations():
        rows = list(
            MetricSnapshot.objects.all_orgs().filter(organization=world.organization)
        )
    return {row.dimension["point"]: float(row.value) for row in rows}


def _active_headcount(world) -> int:
    from apps.employees.models import Employee

    from tests.conftest import across_organizations

    # Counts one organization while another may be bound, so it reads the way
    # the platform does -- see `across_organizations`.
    with across_organizations():
        return (
            Employee.objects.all_orgs()
            .filter(organization=world.organization, is_active=True)
            .exclude(date_of_exit__lt=dt.date.today())
            .count()
        )


@pytest.fixture
def org_b_is_bigger(org_b):  # noqa: F811
    from apps.employees.models import Employee

    with acting_as(None, organization=org_b.organization):
        Employee.objects.create(
            employee_code="EMP900",
            first_name="Extra",
            last_name="Hire",
            department=org_b.department,
            designation=org_b.designation,
            location=org_b.location,
            level=org_b.level,
            date_of_joining=dt.date(2024, 1, 1),
        )
    return org_b


def test_one_organizations_refresh_writes_only_its_own_snapshots(
    org_a, org_b_is_bigger  # noqa: F811
):
    org_b = org_b_is_bigger
    assert _active_headcount(org_a) != _active_headcount(org_b), (
        "the organizations need different headcounts for this to prove anything"
    )

    written = refresh_snapshots_for_organization(org_a.organization.pk)

    assert written > 0
    assert not _snapshots(org_b), "org A's refresh wrote rows owned by org B"

    latest = max(_snapshots(org_a).items())
    assert latest[1] == _active_headcount(org_a), (
        f"org A's headcount snapshot is {latest[1]}, not its own "
        f"{_active_headcount(org_a)} -- it counted another organization's staff"
    )


def test_a_second_organizations_refresh_leaves_the_first_untouched(
    org_a, org_b_is_bigger  # noqa: F811
):
    """
    The write side of the same question.

    The uniqueness constraint includes the organization, but the lookup in
    `update_or_create` is what decides which row gets overwritten. A lookup
    without it would find org A's row and replace A's value with B's.
    """
    org_b = org_b_is_bigger

    refresh_snapshots_for_organization(org_a.organization.pk)
    a_before = _snapshots(org_a)

    refresh_snapshots_for_organization(org_b.organization.pk)

    assert _snapshots(org_a) == a_before
    assert max(_snapshots(org_b).items())[1] == _active_headcount(org_b)


def test_a_refresh_replaces_the_previous_window_rather_than_accumulating(org_a):  # noqa: F811
    """
    `period_start` moves with the date, so without pruning every nightly run
    would add a fresh year of rows beside the last one, forever.
    """
    from apps.reporting.services import refresh_snapshots

    today = dt.date.today()
    with acting_as(None, organization=org_a.organization):
        refresh_snapshots(org_a.organization, as_of=today - dt.timedelta(days=1))
        refresh_snapshots(org_a.organization, as_of=today)

    with acting_as(None, organization=org_a.organization):
        starts = set(
            MetricSnapshot.objects.all_orgs()
            .filter(organization=org_a.organization)
            .values_list("period_start", flat=True)
        )
    assert len(starts) == 1


# ------------------------------------------------------------- the read path


def _poison(organization=None) -> None:
    """
    Overwrite every stored value with a number no headcount could be.

    `update()` deliberately, not `save()`: `computed_at` is `auto_now`, so
    saving would refresh it and quietly re-satisfy the freshness rule these
    tests are about.
    """
    rows = MetricSnapshot.objects.all_orgs().filter(metric_key=TREND)
    if organization is not None:
        rows = rows.filter(organization=organization)
    rows.update(value=IMPOSSIBLE)


def test_a_caller_with_organisation_wide_scope_is_served_the_snapshot(
    people, ceo_user, organization
):
    """The read path exists at all — the number comes back from storage."""
    refresh_snapshots(organization)
    _poison()

    result = services.compute(TREND, user=ceo_user)

    assert result.source == "snapshot"
    assert result.points, "a snapshot was served but it carried no points"
    assert {point.value for point in result.points} == {IMPOSSIBLE}


def test_the_snapshot_says_when_it_was_computed_not_when_it_was_asked_for(
    people, ceo_user, organization
):
    """
    `generated_at` must be the moment the number was COMPUTED.

    Stamping it with the request time would present last night's figure as
    one produced this second, which is the single most misleading thing a
    cached dashboard can do.
    """
    refresh_snapshots(organization)
    # Each row is written by its own save, so they are stamped microseconds
    # apart; the result reports the newest of them.
    newest = max(
        MetricSnapshot.objects.all_orgs()
        .filter(metric_key=TREND)
        .values_list("computed_at", flat=True)
    )

    result = services.compute(TREND, user=ceo_user)

    assert result.generated_at == newest
    assert result.generated_at < timezone.now()


def test_a_department_head_is_never_served_the_organisation_wide_snapshot(
    people, organization
):
    """
    THE CASE THAT MATTERS.

    A stored snapshot is the whole organisation's number. Serving it to a
    caller who may only see their own department hands them everyone else's
    figures through the cache — the disclosure this design spends its whole
    docstring budget avoiding. They get the live computation instead.
    """
    refresh_snapshots(organization)
    _poison()

    result = services.compute(TREND, user=people["medical_director"].user)

    assert result.source == "live"
    assert IMPOSSIBLE not in {point.value for point in result.points}
    assert "department" in result.scope_label.lower()


def test_a_stale_snapshot_is_ignored_rather_than_served_as_current(
    people, ceo_user, organization
):
    """A missed refresh makes the dashboard slower, never wrong."""
    refresh_snapshots(organization)
    _poison()
    MetricSnapshot.objects.all_orgs().filter(metric_key=TREND).update(
        computed_at=timezone.now() - services.SNAPSHOT_MAX_AGE - dt.timedelta(hours=1)
    )

    result = services.compute(TREND, user=ceo_user)

    assert result.source == "live"
    assert IMPOSSIBLE not in {point.value for point in result.points}


def test_a_window_nobody_snapshotted_is_computed_live(people, ceo_user, organization):
    """
    The stored window answers one question. A different range is a different
    question, and answering it from these rows would be a wrong number rather
    than a slow one.
    """
    refresh_snapshots(organization)
    _poison()

    today = dt.date.today()
    result = services.compute(
        TREND, user=ceo_user, start=today - dt.timedelta(days=30), end=today
    )

    assert result.source == "live"
    assert IMPOSSIBLE not in {point.value for point in result.points}


def test_another_organizations_snapshot_is_never_served(
    people, ceo_user, organization, org_a  # noqa: F811
):
    """
    Tenancy on the read path, asked directly.

    Two things now filter this query: the tenant manager, and the org predicate
    the read path applies from the caller's resolved context. Asserted on the
    value, so it holds whichever one is doing the work.
    """
    refresh_snapshots_for_organization(org_a.organization.pk)
    _poison(organization=org_a.organization)

    result = services.compute(TREND, user=ceo_user)

    assert result.source == "live", "another organization's rows were served"
    assert IMPOSSIBLE not in {point.value for point in result.points}


def test_the_stored_answer_and_the_live_one_agree(people, ceo_user, organization):
    """
    The positive control for every refusal above.

    Without it, all of this would also be satisfied by a read path that never
    served anything, or one that served points in the wrong order.
    """
    refresh_snapshots(organization)
    from_storage = services.compute(TREND, user=ceo_user)

    MetricSnapshot.objects.all_orgs().filter(metric_key=TREND).hard_delete()
    live = services.compute(TREND, user=ceo_user)

    assert from_storage.source == "snapshot" and live.source == "live"
    assert [(p.key, p.label, p.value) for p in from_storage.points] == [
        (p.key, p.label, p.value) for p in live.points
    ]


def test_a_second_run_on_the_same_day_drops_a_point_that_has_gone_away(org_a):  # noqa: F811
    """
    A re-run must leave exactly what the metric produces, not a union with
    what it produced last time.

    Pruning used to be by WINDOW -- delete rows whose `period_start` is older
    than today's. A second run on the same day writes the same window, so any
    point that has since disappeared was never deleted and went on being
    served from a row nothing updates. A reversed payroll run is the real
    version: its month vanishes from the live series and lingers in the cache.
    Re-runs are ordinary -- a retried subtask, or an operator refreshing after
    a correction.
    """
    from apps.reporting.services import refresh_snapshots

    today = dt.date.today()
    with acting_as(None, organization=org_a.organization):
        refresh_snapshots(org_a.organization, as_of=today)

    own = MetricSnapshot.objects.all_orgs().filter(
        organization=org_a.organization, metric_key=TREND
    )
    with acting_as(None, organization=org_a.organization):
        stale = own.order_by("sequence").first()
        assert stale is not None, "nothing was stored, so this proves nothing"
        # A point the next run cannot produce: the metric's keys are month ends.
        own.filter(pk=stale.pk).update(dimension={"point": "not-a-month-end"})

        refresh_snapshots(org_a.organization, as_of=today)

    with acting_as(None, organization=org_a.organization):
        assert not own.filter(pk=stale.pk).exists(), (
            "a point that disappeared between runs survived the re-run and is "
            "still being served"
        )
        # Positive control: the run that dropped it wrote a full, ordered series.
        keys = list(own.order_by("sequence").values_list("dimension", flat=True))
    assert keys and all(k["point"] != "not-a-month-end" for k in keys)


def test_a_snapshotable_metric_reads_only_the_resource_it_declares(org_a, monkeypatch):  # noqa: F811
    """
    Guards the rule that decides who may be served a snapshot.

    `compute()` serves a stored snapshot to a caller holding ALL scope on the
    metric's DECLARED resource. Live, each queryset inside the metric is scoped
    by the resource IT names. Those agree only while a metric reads nothing but
    what it declares -- and if one ever reaches a second resource, a caller
    with ALL on the declared one and a narrower scope on the other would be
    handed the organisation-wide figure from storage and their own, smaller one
    live. The cache would decide what they are allowed to see.

    So: a snapshotable metric may scope by its own resource and no other.
    """
    from apps.reporting.registry import all_specs
    from apps.reporting.services import MetricContext

    asked: dict[str, set[str]] = {}
    original = MetricContext.scoped

    def recording(self, queryset, resource, action="view"):
        asked.setdefault(self.spec.key, set()).add(str(resource))
        return original(self, queryset, resource, action)

    monkeypatch.setattr(MetricContext, "scoped", recording)

    today = dt.date.today()
    params = {"start": today - dt.timedelta(days=365), "end": today, "group_by": ""}
    snapshotable = [spec for spec in all_specs() if spec.snapshotable]
    assert snapshotable, "no snapshotable metric was found, so this proves nothing"

    with acting_as(None, organization=org_a.organization):
        for spec in snapshotable:
            ctx = MetricContext(
                user=None, spec=spec, unrestricted=True,
                organization=org_a.organization,
            )
            list(spec.fn(ctx, params))

            reached = asked.get(spec.key, set())
            assert reached, f"{spec.key} scoped nothing, so this proves nothing"
            assert reached == {str(spec.resource)}, (
                f"{spec.key} is snapshotable and declares {spec.resource}, but "
                f"reads {sorted(reached)}. A snapshot of it would be served on "
                f"ALL scope over {spec.resource} alone."
            )
