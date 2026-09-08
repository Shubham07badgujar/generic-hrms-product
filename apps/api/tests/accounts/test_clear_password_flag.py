"""
The flag-clearing command: dry by default, audited, and reversible.

This runs against production accounts, so the properties that matter are the
boring ones — it changes nothing unless told to, it changes only what it
named, it writes down what it did, and it can put it back.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def flagged(make_user):
    """Three accounts carrying the flag, one not."""
    users = [make_user("employee", email=f"seed{i}@example.test") for i in range(3)]
    User.objects.filter(pk__in=[u.pk for u in users]).update(must_change_password=True)
    settled = make_user("recruiter", email="settled@example.test")
    return users, settled


def test_a_dry_run_changes_nothing(flagged, capsys):
    users, _ = flagged

    call_command("clear_password_change_flag")

    assert User.objects.filter(must_change_password=True).count() == len(users)
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_clears_every_flagged_account(flagged, tmp_path):
    users, settled = flagged
    manifest = tmp_path / "m.json"

    call_command("clear_password_change_flag", "--apply", f"--manifest={manifest}")

    assert User.objects.filter(must_change_password=True).count() == 0
    # And the account that never carried it is untouched.
    settled.refresh_from_db()
    assert settled.must_change_password is False


def test_only_the_named_accounts_are_touched(flagged, tmp_path):
    users, _ = flagged

    call_command(
        "clear_password_change_flag",
        "--apply",
        "--email=seed0@example.test",
        f"--manifest={tmp_path / 'm.json'}",
    )

    still_flagged = set(
        User.objects.filter(must_change_password=True).values_list("email", flat=True)
    )
    assert still_flagged == {"seed1@example.test", "seed2@example.test"}


def test_every_change_is_audited_with_before_and_after(flagged, tmp_path):
    call_command("clear_password_change_flag", "--apply", f"--manifest={tmp_path / 'm.json'}")

    rows = AuditLog.objects.filter(after__event="password_change_flag_cleared")
    assert rows.count() == 3
    row = rows.first()
    assert row.before == {"must_change_password": True}
    assert row.after["must_change_password"] is False


def test_the_actor_is_recorded_when_given(flagged, make_user, tmp_path):
    admin = make_user("admin", email="admin@example.test")

    call_command(
        "clear_password_change_flag",
        "--apply",
        f"--actor={admin.email}",
        f"--manifest={tmp_path / 'm.json'}",
    )

    row = AuditLog.objects.filter(after__event="password_change_flag_cleared").first()
    assert row.actor_id == admin.pk


def test_the_manifest_names_exactly_what_changed(flagged, tmp_path):
    users, _ = flagged
    manifest = tmp_path / "m.json"

    call_command("clear_password_change_flag", "--apply", f"--manifest={manifest}")

    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(written["user_ids"]) == {str(u.pk) for u in users}
    assert "settled@example.test" not in written["emails"]


def test_rollback_restores_exactly_those_accounts(flagged, tmp_path):
    users, settled = flagged
    manifest = tmp_path / "m.json"
    call_command("clear_password_change_flag", "--apply", f"--manifest={manifest}")

    call_command(
        "clear_password_change_flag", "--rollback", "--apply", f"--manifest={manifest}"
    )

    restored = set(
        User.objects.filter(must_change_password=True).values_list("email", flat=True)
    )
    assert restored == {u.email for u in users}
    settled.refresh_from_db()
    assert settled.must_change_password is False


def test_rollback_is_also_dry_by_default(flagged, tmp_path):
    manifest = tmp_path / "m.json"
    call_command("clear_password_change_flag", "--apply", f"--manifest={manifest}")

    call_command("clear_password_change_flag", "--rollback", f"--manifest={manifest}")

    assert User.objects.filter(must_change_password=True).count() == 0


def test_a_future_account_still_gets_the_flag(flagged, tmp_path, make_user, org):
    """
    The point of the whole exercise: clearing the past does not disarm the
    feature. An employee created afterwards is flagged exactly as designed.
    """
    from apps.employees.models import Employee
    from apps.employees.services.creation import create_employee
    from core.access.catalog import DepartmentKind, Layer

    call_command("clear_password_change_flag", "--apply", f"--manifest={tmp_path / 'm.json'}")

    hr = make_user("hr_head", email="hrboss@example.test")
    Employee.objects.create(
        employee_code="EMP07900",
        user=hr,
        first_name="Hema",
        department=org["departments"][DepartmentKind.HR],
        date_of_joining="2020-01-01",
    )
    director_user = make_user("medical_director", email="md@example.test")
    director = Employee.objects.create(
        employee_code="EMP07901",
        user=director_user,
        first_name="Meera",
        department=org["departments"][DepartmentKind.MEDICAL],
        date_of_joining="2019-01-01",
    )

    result = create_employee(
        actor=hr,
        first_name="Future",
        email="future@example.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        location_id=org["location"].pk,
        reporting_manager_id=director.pk,
        date_of_joining="2026-10-01",
    )

    assert result.user.must_change_password is True
