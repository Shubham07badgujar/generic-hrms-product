"""Fixtures for the attendance / eSSL integration tests."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone


#: A canned eTimeTrackLite SOAP reply, shaped exactly like the live service's
#: (verified against the company's own server during planning).
def soap_reply(rows: list[str], result: str = "Success") -> str:
    payload = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<GetTransactionsLogResponse xmlns="http://tempuri.org/">'
        f"<GetTransactionsLogResult>{result}</GetTransactionsLogResult>"
        f"<strDataList>{payload}</strDataList>"
        "</GetTransactionsLogResponse>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


class FakeTransport:
    """Replays canned replies and records what was posted."""

    def __init__(self, replies: list[str] | None = None, error: Exception | None = None):
        self.replies = list(replies or [])
        self.error = error
        self.requests: list[dict] = []

    def post(self, url, *, headers, body):
        self.requests.append({"url": url, "headers": headers, "body": body})
        if self.error is not None:
            raise self.error
        if not self.replies:
            return soap_reply([])
        return self.replies.pop(0)


@pytest.fixture
def fake_transport():
    return FakeTransport


@pytest.fixture
def essl_settings(settings):
    settings.ESSL_INTEGRATION_ENABLED = True
    settings.ESSL_BASE_URL = "http://essl.example.test:81"
    settings.ESSL_USERNAME = "apiuser"
    settings.ESSL_PASSWORD = "apipass"
    settings.ESSL_TIMEZONE = "Asia/Kolkata"
    return settings


@pytest.fixture
def device(db, org):
    from apps.attendance.models import AttendanceDevice

    return AttendanceDevice.objects.create(
        name="Borivali Branch", serial_number="SN-BOR-01", location=org["location"]
    )


@pytest.fixture
def shift_rule(db, org):
    """The company chart's clinic rule, bound to the shared test location."""
    from apps.attendance.models import ShiftRule

    return ShiftRule.objects.create(
        location=org["location"],
        start_time=dt.time(10, 0),
        end_time=dt.time(19, 0),
        grace_minutes=15,
        allowed_late_per_month=3,
    )


@pytest.fixture
def worker(db, org, roles):
    """An employee at the shared location, mapped to eSSL user '101'."""
    from apps.attendance.models import EsslEmployeeLink
    from apps.employees.models import Employee

    employee = Employee.objects.create(
        employee_code="EMP09001",
        first_name="Punch",
        last_name="Tester",
        department=org["departments"]["operations"],
        location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    EsslEmployeeLink.objects.create(essl_user_id="101", employee=employee)
    return employee


@pytest.fixture
def punch_day(device, worker):
    """Insert punches for `worker` on a given local date via the raw store."""
    from apps.attendance.models import RawPunch

    def _make(date: dt.date, times: list[str], employee=worker, mapped=True):
        tz = timezone.get_current_timezone()
        rows = []
        for hhmm in times:
            hour, minute = (int(x) for x in hhmm.split(":"))
            rows.append(
                RawPunch.objects.create(
                    device=device,
                    essl_user_id="101" if mapped else "999",
                    punched_at=dt.datetime(
                        date.year, date.month, date.day, hour, minute, tzinfo=tz
                    ),
                    employee=employee if mapped else None,
                    raw=f"101\t{date} {hhmm}:00",
                )
            )
        return rows

    return _make
