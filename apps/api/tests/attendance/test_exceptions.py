"""
The hours-first policy and HR's exception review.

The claims: a late made up by completing the day never counts against the
month; only counted lates escalate to a half day; the exceptions filter
shows exactly what the engine flagged and nobody has reviewed; overrides
demand a reason and land in the audit log with before/after; a
regularization cannot silently undo HR's manual ruling; and policy edits
to shift rules are themselves audited.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.attendance.models import (
    AttendanceRecord,
    RecordSource,
    RecordStatus,
    RegularizationRequest,
)
from apps.attendance.services.calculation import recompute_day
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db

# 2026-08-25 is a Tuesday.
DAY = dt.date(2026, 8, 25)


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def hr(make_user, org, roles):
    """An HR Head WITH an employee record — roles fail closed without one."""
    import itertools

    from apps.employees.models import Employee

    counter = itertools.count(9100)

    def _make(email):
        user = make_user("hr_head", email=email)
        Employee.objects.create(
            employee_code=f"EMP0{next(counter)}",
            first_name="Hema",
            user=user,
            department=org["departments"]["hr"],
            date_of_joining=dt.date(2020, 1, 1),
        )
        return user

    return _make


# ------------------------------------------------------- compensated lates


def test_a_late_made_up_by_full_hours_is_compensated(worker, shift_rule, punch_day):
    """The reported case: in 10:24, out 19:28 — 9h04 worked, no penalty."""
    punch_day(DAY, ["10:24", "19:28"])
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.is_late is True          # visible to HR
    assert record.late_counted is False    # but never charged
    assert "compensated" in record.notes


def test_exactly_full_hours_still_compensates(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:24", "19:24"])  # 9h00 exactly
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.late_counted is False


def test_a_late_without_full_hours_is_counted(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:24", "19:00"])  # 8h36 < 9h
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.is_late is True
    assert record.late_counted is True


def test_compensated_lates_never_feed_the_escalation(worker, shift_rule, punch_day):
    """Three counted lates on record; a compensated fourth stays Present,
    and the NEXT uncompensated late is the one that costs the half day."""
    for day in (17, 18, 19):  # Mon-Wed, all late and short
        date = dt.date(2026, 8, day)
        punch_day(date, ["10:30", "19:00"])
        assert recompute_day(worker, date).late_counted is True

    compensated = dt.date(2026, 8, 20)
    punch_day(compensated, ["10:30", "19:31"])  # 9h01 — made up
    record = recompute_day(worker, compensated)
    assert record.status == RecordStatus.PRESENT
    assert record.late_counted is False

    fourth = dt.date(2026, 8, 21)
    punch_day(fourth, ["10:30", "19:00"])
    record = recompute_day(worker, fourth)
    assert record.status == RecordStatus.HALF_DAY
    assert "no. 4" in record.notes


def test_a_single_punch_day_is_flagged_not_guessed(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:05"])
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.worked_minutes == 0
    assert "unverifiable" in record.notes


def test_recompute_clears_the_review_state(worker, shift_rule, punch_day, hr):
    punch_day(DAY, ["10:30", "19:00"])
    record = recompute_day(worker, DAY)
    reviewer = hr("hr1@example.test")
    record.reviewed_by = reviewer
    record.reviewed_at = dt.datetime(2026, 8, 26, 10, 0, tzinfo=dt.timezone.utc)
    record.save(update_fields=["reviewed_by", "reviewed_at"])

    record = recompute_day(worker, DAY)  # device row: recompute allowed
    assert record.reviewed_by is None and record.reviewed_at is None


# ------------------------------------------------------- exceptions filter


def test_the_exceptions_filter_shows_the_flagged_and_unreviewed(
    worker, shift_rule, punch_day, hr
):
    punch_day(dt.date(2026, 8, 24), ["10:24", "19:28"])   # compensated — NOT an exception
    punch_day(dt.date(2026, 8, 25), ["10:24", "19:00"])   # counted late — exception
    punch_day(dt.date(2026, 8, 26), ["10:05", "13:00"])   # short-hours half day — exception
    punch_day(dt.date(2026, 8, 27), ["10:05"])            # single punch — exception
    for day in (24, 25, 26, 27):
        recompute_day(worker, dt.date(2026, 8, day))
    recompute_day(worker, dt.date(2026, 8, 28))           # absent — exception

    client = _client_for(hr("hr2@example.test"))
    rows = client.get("/api/v1/attendance-records/", {"exception": "true"}).data["data"]
    dates = sorted(row["date"] for row in rows)
    assert dates == ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]

    # Acknowledging one removes it from the queue and audits the act.
    target = next(r for r in rows if r["date"] == "2026-08-25")
    response = client.post(f"/api/v1/attendance-records/{target['id']}/acknowledge/", {})
    assert response.status_code == 200
    assert response.data["reviewed_at"] is not None
    remaining = client.get("/api/v1/attendance-records/", {"exception": "true"}).data["data"]
    assert sorted(r["date"] for r in remaining) == ["2026-08-26", "2026-08-27", "2026-08-28"]
    assert AuditLog.objects.filter(
        entity_type="attendance.AttendanceRecord", entity_id=str(target["id"]),
        after__reviewed=True,
    ).exists()

    # Acknowledging twice is refused.
    assert client.post(
        f"/api/v1/attendance-records/{target['id']}/acknowledge/", {}
    ).status_code == 400


def test_employees_cannot_acknowledge(worker, shift_rule, punch_day, make_user, roles):
    punch_day(DAY, ["10:24", "19:00"])
    record = recompute_day(worker, DAY)
    employee_user = make_user("employee", email="just.staff@example.test")
    client = _client_for(employee_user)
    assert client.post(
        f"/api/v1/attendance-records/{record.pk}/acknowledge/", {}
    ).status_code in (403, 404)


# ------------------------------------------------------------- overrides


def test_an_override_needs_a_reason_and_is_fully_audited(
    worker, shift_rule, punch_day, hr
):
    punch_day(DAY, ["10:24", "18:00"])
    record = recompute_day(worker, DAY)
    record.status = RecordStatus.HALF_DAY  # simulate an escalated day
    record.late_counted = True
    record.save(update_fields=["status", "late_counted"])

    hr_user = hr("hr3@example.test")
    client = _client_for(hr_user)

    # No reason → refused.
    response = client.patch(
        f"/api/v1/attendance-records/{record.pk}/",
        {"status": "present", "notes": ""},
        format="json",
    )
    assert response.status_code == 400

    # With a reason → manual, pardoned, audited with before/after.
    response = client.patch(
        f"/api/v1/attendance-records/{record.pk}/",
        {"status": "present", "notes": "Doctor's appointment, informed in advance."},
        format="json",
    )
    assert response.status_code == 200
    record.refresh_from_db()
    assert record.status == RecordStatus.PRESENT
    assert record.source == RecordSource.MANUAL
    assert record.late_counted is False           # the month stops charging it
    assert record.reviewed_by == hr_user

    # The EXPLICIT override event (the one carrying the reason) — the generic
    # model-diff row lands in the same microsecond, so ordering alone cannot
    # pick between them deterministically.
    row = AuditLog.objects.filter(
        entity_type="attendance.AttendanceRecord",
        entity_id=str(record.pk),
        before__status="half_day",
        after__status="present",
        reason__icontains="appointment",
    ).first()
    assert row is not None
    assert row.actor_email == hr_user.email

    # And the machine keeps its hands off from now on.
    assert recompute_day(worker, DAY) is None


# -------------------------------------------------------- regularization


def test_regularizing_over_a_manual_ruling_is_refused(
    worker, shift_rule, punch_day, hr
):
    AttendanceRecord.objects.create(
        employee=worker, date=DAY, status=RecordStatus.ABSENT,
        source=RecordSource.MANUAL, notes="HR: unexplained absence, stands.",
    )
    request = RegularizationRequest.objects.create(
        employee=worker, date=DAY, reason="I was at the Thane branch."
    )
    response = _client_for(hr("hr4@example.test")).post(
        f"/api/v1/regularizations/{request.pk}/approve/", {}
    )
    assert response.status_code == 400
    assert "manually corrected" in str(response.data)
    record = AttendanceRecord.objects.active().get(employee=worker, date=DAY)
    assert record.status == RecordStatus.ABSENT and record.source == RecordSource.MANUAL


def test_an_approved_regularization_pardons_the_counted_late(
    worker, shift_rule, punch_day, hr
):
    punch_day(DAY, ["10:40", "18:00"])
    record = recompute_day(worker, DAY)
    assert record.late_counted is True

    request = RegularizationRequest.objects.create(
        employee=worker, date=DAY, reason="Client visit before reaching the clinic."
    )
    response = _client_for(hr("hr5@example.test")).post(
        f"/api/v1/regularizations/{request.pk}/approve/", {}
    )
    assert response.status_code == 200
    record.refresh_from_db()
    assert record.source == RecordSource.REGULARIZED
    assert record.late_counted is False
    assert AuditLog.objects.filter(
        entity_type="attendance.AttendanceRecord", entity_id=str(record.pk),
        after__source="regularized",
    ).exists()


# ------------------------------------------------------------ shift rules


def test_hr_edits_a_shift_rule_and_it_is_audited(shift_rule, hr):
    client = _client_for(hr("hr6@example.test"))

    rows = client.get("/api/v1/essl/shift-rules/").data
    assert any(str(shift_rule.pk) == str(row["id"]) for row in rows)

    response = client.patch(
        f"/api/v1/essl/shift-rules/{shift_rule.pk}/",
        {"grace_minutes": 10, "full_day_hours": "8.5"},
        format="json",
    )
    assert response.status_code == 200
    shift_rule.refresh_from_db()
    assert shift_rule.grace_minutes == 10
    assert shift_rule.full_day_hours == Decimal("8.5")
    assert AuditLog.objects.filter(
        entity_type="attendance.ShiftRule",
        before__grace_minutes="15",
        after__grace_minutes="10",
    ).exists()

    # Nonsense is refused: a full day below the half-day line.
    assert client.patch(
        f"/api/v1/essl/shift-rules/{shift_rule.pk}/",
        {"full_day_hours": "4.0"},
        format="json",
    ).status_code == 400


def test_reseeding_never_reverts_an_hr_edit(shift_rule):
    from apps.attendance.seeds import seed_shift_rules

    shift_rule.grace_minutes = 5
    shift_rule.save(update_fields=["grace_minutes"])
    seed_shift_rules()
    shift_rule.refresh_from_db()
    assert shift_rule.grace_minutes == 5


def test_employees_cannot_touch_shift_rules(make_user, roles, shift_rule):
    user = make_user("employee", email="rank.file@example.test")
    client = _client_for(user)
    assert client.get("/api/v1/essl/shift-rules/").status_code == 403
    assert client.patch(
        f"/api/v1/essl/shift-rules/{shift_rule.pk}/", {"grace_minutes": 0}, format="json"
    ).status_code in (403, 404)


# ------------------------------------------------------------ payroll seam


def test_monthly_summary_treats_a_compensated_late_as_a_full_day(
    essl_settings, worker, shift_rule, punch_day, settings
):
    settings.ATTENDANCE_AFFECTS_PAYROLL = True
    punch_day(DAY, ["10:24", "19:28"])
    recompute_day(worker, DAY)

    from apps.attendance.services import monthly_summary

    summary = monthly_summary(worker, 2026, 8)
    # One late shown for information; nothing docked for it.
    assert summary.late_days == 1
    assert summary.half_days == 0


# ------------------------------------------------------ recompute command


def test_the_recompute_command_is_bounded_and_respects_hr(
    worker, shift_rule, punch_day
):
    from django.core.management import CommandError, call_command

    punch_day(DAY, ["10:24", "19:28"])
    recompute_day(worker, DAY)
    # HR's manual ruling on another day must survive the sweep.
    manual = AttendanceRecord.objects.create(
        employee=worker, date=dt.date(2026, 8, 26),
        status=RecordStatus.ABSENT, source=RecordSource.MANUAL,
        notes="HR: stands.",
    )

    with pytest.raises(CommandError):
        call_command(
            "recompute_attendance", **{"date_from": "2026-01-01", "date_to": "2026-12-31"}
        )

    call_command(
        "recompute_attendance",
        **{"date_from": "2026-08-24", "date_to": "2026-08-28",
           "employee_code": worker.employee_code},
    )
    manual.refresh_from_db()
    assert manual.status == RecordStatus.ABSENT
    assert manual.source == RecordSource.MANUAL
    day = AttendanceRecord.objects.active().get(employee=worker, date=DAY)
    assert day.status == RecordStatus.PRESENT and day.late_counted is False


# ------------------------------------------------------------------ export


def test_hr_exports_an_employees_range_as_csv_and_xlsx(
    worker, shift_rule, punch_day, hr
):
    punch_day(dt.date(2026, 8, 24), ["10:24", "19:28"])  # compensated late
    punch_day(dt.date(2026, 8, 25), ["10:05", "19:00"])  # ordinary day
    for day in (24, 25):
        recompute_day(worker, dt.date(2026, 8, day))

    client = _client_for(hr("hr7@example.test"))
    params = {
        "employee": str(worker.pk),
        "date__gte": "2026-08-24", "date__lte": "2026-08-26", "fmt": "csv",
    }

    response = client.get("/api/v1/attendance-records/export/", params)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert worker.employee_code in response["Content-Disposition"]
    body = response.content.decode("utf-8-sig")
    lines = body.strip().splitlines()
    assert lines[0].startswith("Date,Day,Employee Code")
    assert len(lines) == 3  # header + two days
    assert "compensated" in body and "10:24" in body and "19:28" in body

    response = client.get(
        "/api/v1/attendance-records/export/", {**params, "fmt": "xlsx"}
    )
    assert response.status_code == 200
    assert response.content[:2] == b"PK"  # a real zip container
    assert "spreadsheetml" in response["Content-Type"]

    # The export is audited against the employee.
    assert AuditLog.objects.filter(
        entity_type="employees.Employee", entity_id=str(worker.pk),
        after__export="attendance",
    ).exists()

    # Bounds hold.
    assert client.get(
        "/api/v1/attendance-records/export/",
        {"date__gte": "2026-01-01", "date__lte": "2026-12-31"},
    ).status_code == 400
    assert client.get("/api/v1/attendance-records/export/", {}).status_code == 400


def test_an_employee_exports_only_their_own_days(
    worker, shift_rule, punch_day, make_user, roles, org
):
    from apps.employees.models import Employee

    punch_day(DAY, ["10:05", "19:00"])
    recompute_day(worker, DAY)

    other_user = make_user("employee", email="other.staff@example.test")
    Employee.objects.create(
        employee_code="EMP09050", first_name="Other", user=other_user,
        department=org["departments"]["operations"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    response = _client_for(other_user).get(
        "/api/v1/attendance-records/export/",
        {"date__gte": "2026-08-24", "date__lte": "2026-08-26", "fmt": "csv"},
    )
    assert response.status_code == 200
    body = response.content.decode("utf-8-sig")
    assert worker.employee_code not in body  # scope: their rows only


def test_the_punches_behind_a_day_are_listed(worker, shift_rule, punch_day, hr):
    punch_day(DAY, ["10:24", "13:02", "19:28"])
    record = recompute_day(worker, DAY)

    client = _client_for(hr("hr8@example.test"))
    response = client.get(f"/api/v1/attendance-records/{record.pk}/punches/")
    assert response.status_code == 200
    times = [row["time"] for row in response.data]
    assert times == ["10:24", "13:02", "19:28"]
    assert all(row["device"] for row in response.data)
