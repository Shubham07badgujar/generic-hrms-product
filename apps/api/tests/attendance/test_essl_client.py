"""
The eSSL Web API client, against the wire format the live server showed us.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.attendance.services import essl_client
from tests.attendance.conftest import FakeTransport, soap_reply

pytestmark = pytest.mark.django_db


def _window():
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return (
        dt.datetime(2026, 8, 25, 0, 0, tzinfo=tz),
        dt.datetime(2026, 8, 26, 0, 0, tzinfo=tz),
    )


def test_the_request_matches_the_services_documented_contract(essl_settings):
    transport = FakeTransport([soap_reply([])])
    essl_client.fetch_punches("SN-1", *_window(), transport=transport)

    sent = transport.requests[0]
    assert sent["url"] == "http://essl.example.test:81/iclock/WebAPIService.asmx"
    assert sent["headers"]["SOAPAction"] == '"http://tempuri.org/GetTransactionsLog"'
    for field in (
        "<FromDateTime>", "<ToDateTime>", "<SerialNumber>SN-1</SerialNumber>",
        "<UserName>apiuser</UserName>", "<UserPassword>apipass</UserPassword>",
        "<strDataList>",
    ):
        assert field in sent["body"]


def test_rows_parse_with_the_device_timezone_attached(essl_settings):
    transport = FakeTransport([
        soap_reply([
            "101\t2026-08-25 10:03:11",
            "102\t2026-08-25 10:04:00\textra-field",
            "malformed-row-no-tab",
            "103\tnot-a-datetime",
        ])
    ])
    rows = essl_client.fetch_punches("SN-1", *_window(), transport=transport)

    # Good rows survive, malformed rows are skipped, nothing raises.
    assert [r.essl_user_id for r in rows] == ["101", "102"]
    assert rows[0].punched_at.tzinfo is not None
    assert rows[0].punched_at.utcoffset() == dt.timedelta(hours=5, minutes=30)
    assert rows[1].raw == "102\t2026-08-25 10:04:00\textra-field"


def test_the_unauthorised_verdict_becomes_an_actionable_message(essl_settings):
    transport = FakeTransport([soap_reply([], result="Unathorised User")])
    with pytest.raises(essl_client.EsslError, match="own user list"):
        essl_client.fetch_punches("SN-1", *_window(), transport=transport)


def test_disabled_configuration_reads_as_disabled(settings):
    settings.ESSL_INTEGRATION_ENABLED = False
    assert essl_client.essl_enabled() is False
    settings.ESSL_INTEGRATION_ENABLED = True
    settings.ESSL_BASE_URL = ""
    assert essl_client.essl_enabled() is False
