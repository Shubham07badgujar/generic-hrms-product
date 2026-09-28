"""
The end of a customer: archive, then purge -- the only hard delete in the product.

Two organizations are built by the tenancy suite's own builder, populated across
dozens of tables with real uploaded files, so "purge removed everything" is a
claim about a broad organization rather than an empty one. The tests that matter
most, in order of the damage a regression would do:

  * purging A leaves every row of B exactly as it was -- counted per table;
  * purging A leaves no row of A in any organization-owned table, and the
    guard asserts A actually HAD rows in many tables first;
  * the audit trail survives with its payloads scrubbed, plus one terminal
    record that holds counts, not people;
  * every precondition refuses before writing anything.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.platform.services.lifecycle import (
    ARCHIVE_AFTER_CANCEL_DAYS,
    PURGE_AFTER_ARCHIVE_DAYS,
    LifecycleError,
    _organization_tables,
    archive_organization,
    purge_organization,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def media(settings, tmp_path):
    """Requested FIRST by the fixtures below, so the worlds' files land here."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def worlds(media, org_a, org_b):
    return org_a, org_b


def _counts(organization) -> dict[str, int]:
    from apps.platform.services.lifecycle import _count

    from .conftest import across_organizations

    # Ground truth for the assertions below, so it reads as the platform does.
    with across_organizations():
        return {m._meta.label: _count(m, organization) for m in _organization_tables()}


def _archived_long_ago(organization):
    from apps.organization.models import OrgStatus

    organization.status = OrgStatus.ARCHIVED
    organization.archived_at = timezone.now() - dt.timedelta(days=PURGE_AFTER_ARCHIVE_DAYS + 1)
    organization.save(update_fields=["status", "archived_at", "updated_at"])


def _purge(organization, **kwargs):
    return purge_organization(organization, confirm=organization.slug, **kwargs)


# ---------------------------------------------------------------------------
# Purge: what goes, what stays, and that B is untouched
# ---------------------------------------------------------------------------


def test_purging_one_organization_removes_all_of_it_and_none_of_the_other(
    worlds, django_capture_on_commit_callbacks
):
    a, b = worlds
    before_a = _counts(a.organization)
    before_b = _counts(b.organization)
    populated = {label for label, n in before_a.items() if n}
    # Guard the guard: a purge of an empty organization would pass everything
    # below and prove nothing.
    assert len(populated) >= 40, f"A is only populated in {len(populated)} tables"

    _archived_long_ago(a.organization)
    with django_capture_on_commit_callbacks(execute=True):
        _purge(a.organization)

    after_a = _counts(a.organization)
    assert {label: n for label, n in after_a.items() if n} == {}, "rows of A survived"
    assert _counts(b.organization) == before_b, "purging A changed B"


def test_the_logins_go_and_the_other_organizations_stay(worlds):
    from apps.accounts.models import User

    a, b = worlds
    _archived_long_ago(a.organization)
    _purge(a.organization)

    for user in (a.admin, a.hr, a.worker):
        assert not User.objects.filter(pk=user.pk).exists(), f"{user.email} survived"
    for user in (b.admin, b.hr, b.worker):
        assert User.objects.filter(pk=user.pk, is_active=True).exists()


def test_the_organization_row_remains_as_a_tombstone(worlds):
    from apps.organization.models import Organization, OrgStatus

    a, _ = worlds
    _archived_long_ago(a.organization)
    _purge(a.organization)

    tombstone = Organization.objects.get(pk=a.organization.pk)
    assert tombstone.purged_at is not None
    assert tombstone.status == OrgStatus.ARCHIVED


def test_the_audit_trail_survives_scrubbed(worlds):
    """
    Who did what to which entity, and when -- kept. Names, snapshots, reasons
    and addresses -- gone. The terminal record is the one payload left, and it
    holds counts.
    """
    from apps.audit.models import AuditLog

    from .conftest import across_organizations

    a, b = worlds
    # Comparing two organizations' trails, before and after a purge, is the
    # whole test -- so it reads as the platform does throughout.
    with across_organizations():
        b_trail = list(
            AuditLog.objects.filter(organization=b.organization).values_list("pk", "after")
        )
        before = AuditLog.objects.filter(organization=a.organization).count()
    assert before > 0, "the builder should have produced an audit trail"

    _archived_long_ago(a.organization)
    _purge(a.organization)

    with across_organizations():
        trail = list(AuditLog.objects.filter(organization=a.organization))
        b_after = list(
            AuditLog.objects.filter(organization=b.organization).values_list("pk", "after")
        )
    assert len(trail) == before + 1, "rows were lost, or more than one was added"
    terminal = next(row for row in trail if (row.after or {}).get("event") == "organization_purged")
    assert terminal.after["rows_deleted"], "the terminal record should count what went"

    for row in trail:
        if row.pk == terminal.pk:
            continue
        assert row.before is None and row.after is None, f"payload survived on {row.pk}"
        assert row.entity_label == "" and row.reason == "" and row.metadata == {}
        assert row.subject_employee_id is None
        assert row.entity_type and row.entity_id, "the WHAT must survive"

    assert b_after == b_trail, "purging A touched B's trail"


def test_the_files_go_after_commit_and_only_this_organizations(
    worlds, media, django_capture_on_commit_callbacks
):
    a, b = worlds
    a_files = media / "organizations" / str(a.organization.pk)
    b_files = media / "organizations" / str(b.organization.pk)
    assert a_files.exists() and b_files.exists(), "the builder should have uploaded files"

    _archived_long_ago(a.organization)
    with django_capture_on_commit_callbacks(execute=True):
        _purge(a.organization)

    assert not a_files.exists()
    assert b_files.exists()


def test_a_login_that_certified_national_rates_is_deactivated_not_deleted(worlds):
    """
    The deployment-wide statutory rate set references its submitter with
    PROTECT: the four-eyes record of India's PF rates is every customer's.
    Purging one customer must not erase it -- or fail because of it.
    """
    from apps.accounts.models import User
    from apps.statutory.models import StatutoryRuleSet

    a, _ = worlds
    rule_set = StatutoryRuleSet.objects.create(
        statute="pf", effective_from=dt.date(2025, 4, 1), rule_version="test-1",
        submitted_by=a.hr,
    )
    _archived_long_ago(a.organization)
    plan = _purge(a.organization)

    assert a.hr.email in plan.users_spared
    spared = User.objects.get(pk=a.hr.pk)
    assert not spared.is_active and not spared.has_usable_password()
    rule_set.refresh_from_db()
    assert rule_set.submitted_by_id == a.hr.pk


# ---------------------------------------------------------------------------
# Purge: every precondition refuses before writing anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arrange, message",
    [
        ("active", "Only an archived organization"),
        ("archived_recently", "may not be purged before"),
        ("wrong_confirmation", "does not match"),
    ],
)
def test_purge_refuses_and_writes_nothing(worlds, arrange, message):
    from apps.organization.models import OrgStatus

    a, _ = worlds
    confirm = a.organization.slug
    if arrange == "archived_recently":
        a.organization.status = OrgStatus.ARCHIVED
        a.organization.archived_at = timezone.now() - dt.timedelta(days=30)
        a.organization.save(update_fields=["status", "archived_at", "updated_at"])
    elif arrange == "wrong_confirmation":
        _archived_long_ago(a.organization)
        confirm = "someone-else"

    before = _counts(a.organization)
    with pytest.raises(LifecycleError, match=message):
        purge_organization(a.organization, confirm=confirm)
    assert _counts(a.organization) == before


def test_an_organization_is_purged_once(worlds):
    a, _ = worlds
    _archived_long_ago(a.organization)
    _purge(a.organization)
    a.organization.refresh_from_db()
    with pytest.raises(LifecycleError, match="already purged"):
        _purge(a.organization)


def test_the_command_dry_run_writes_nothing_and_refuses_without_confirmation(worlds, capsys):
    a, _ = worlds
    _archived_long_ago(a.organization)
    before = _counts(a.organization)

    call_command("purge_organization", a.organization.slug, "--dry-run")
    assert "Dry run" in capsys.readouterr().out
    with pytest.raises(CommandError, match="--confirm"):
        call_command("purge_organization", a.organization.slug)

    assert _counts(a.organization) == before


def test_the_command_purges_with_the_slug_typed_twice(worlds):
    a, _ = worlds
    _archived_long_ago(a.organization)

    call_command("purge_organization", a.organization.slug, "--confirm", a.organization.slug)

    assert not any(_counts(a.organization).values())


def test_there_is_no_api_route_that_purges(worlds):
    """Purge is a server-side command, deliberately. Not a route, not a flag."""
    from django.urls import get_resolver

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                yield prefix + str(pattern.pattern)

    assert not [route for route in walk(get_resolver().url_patterns) if "purge" in route]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def _cancelled(organization, *, days_ago: int):
    from apps.organization.models import OrgStatus
    from apps.platform.models import Plan, Subscription

    from .conftest import across_organizations

    # A subscription is the PLATFORM's row about a customer -- read-only to the
    # customer's own role -- so the fixture writes it the way billing does.
    with across_organizations():
        plan = Plan.objects.create(code=f"p-{organization.slug}", name="Test")
        Subscription.objects.create(
            organization=organization, plan=plan, status="cancelled",
            cancelled_at=timezone.now() - dt.timedelta(days=days_ago),
        )
    organization.status = OrgStatus.CANCELLED
    organization.save(update_fields=["status", "updated_at"])


def test_archive_waits_for_the_export_window(worlds):
    a, _ = worlds
    _cancelled(a.organization, days_ago=ARCHIVE_AFTER_CANCEL_DAYS - 1)

    with pytest.raises(LifecycleError, match="export window is open"):
        archive_organization(a.organization, actor=None, reason="Contract ended")


def test_archive_after_the_window_is_a_status_change_and_nothing_else(worlds):
    from apps.audit.models import AuditLog
    from apps.organization.models import OrgStatus

    a, _ = worlds
    _cancelled(a.organization, days_ago=ARCHIVE_AFTER_CANCEL_DAYS + 1)
    before = _counts(a.organization)

    archive_organization(a.organization, actor=None, reason="Contract ended")

    a.organization.refresh_from_db()
    assert a.organization.status == OrgStatus.ARCHIVED
    assert a.organization.archived_at is not None
    assert _counts(a.organization) == before, "archiving deleted something"
    from .conftest import across_organizations

    with across_organizations():
        entry = AuditLog.objects.get(organization=a.organization, after__event="archived")
    assert entry.reason == "Contract ended"


@pytest.mark.parametrize("status", ["active", "suspended"])
def test_only_a_cancelled_organization_is_archived(worlds, status):
    a, _ = worlds
    a.organization.status = status
    a.organization.save(update_fields=["status", "updated_at"])
    with pytest.raises(LifecycleError, match="Only a cancelled"):
        archive_organization(a.organization, actor=None, reason="Tidy up")


def test_archive_needs_a_reason(worlds):
    a, _ = worlds
    _cancelled(a.organization, days_ago=ARCHIVE_AFTER_CANCEL_DAYS + 1)
    with pytest.raises(LifecycleError, match="needs a reason"):
        archive_organization(a.organization, actor=None, reason="  ")
