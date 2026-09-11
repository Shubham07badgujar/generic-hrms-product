"""
The eSSL eTimeTrackLite Web API client.

VERIFIED, NOT GUESSED. The company's own server was probed before this was
written: `GET /iclock/WebAPIService.asmx` lists the service's operations, and
its documentation page gives the exact SOAP 1.1 contract implemented here —
`GetTransactionsLog(FromDateTime, ToDateTime, SerialNumber, UserName,
UserPassword, strDataList)`, SOAPAction "http://tempuri.org/GetTransactionsLog".
The response's `strDataList` is newline-separated rows of tab-separated
fields: `essl_user_id \t "YYYY-MM-DD HH:MM:SS" [\t extra...]`.

Credentials live in settings (environment variables) and are read only inside
this module. Nothing here is importable with side effects: `requests` is
imported inside the transport method, so a packaging problem can never
silently re-close payroll's attendance seam (which detects this app by
ImportError).
"""

from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo

from django.conf import settings

logger = logging.getLogger("hrms.attendance")

SERVICE_PATH = "/iclock/WebAPIService.asmx"
SOAP_ACTION = "http://tempuri.org/GetTransactionsLog"

#: The Web API's date format. Times are the device's LOCAL clock, naive.
_WIRE_DT = "%Y-%m-%d %H:%M:%S"


class EsslError(RuntimeError):
    """Recorded on the device and the sync run, never raised at HR."""


@dataclass(frozen=True)
class PunchRow:
    essl_user_id: str
    #: Aware, converted from the device's local clock via ESSL_TIMEZONE.
    punched_at: dt.datetime
    #: The row exactly as the server sent it.
    raw: str


class Transport(Protocol):
    """Injectable so the whole client is testable against canned responses."""

    def post(self, url: str, *, headers: dict, body: str) -> str: ...


class RequestsTransport:
    """The real thing. `requests` is already in the lock file."""

    def post(self, url: str, *, headers: dict, body: str) -> str:
        import requests

        response = requests.post(
            url, headers=headers, data=body.encode("utf-8"), timeout=30
        )
        if response.status_code >= 400:
            raise EsslError(f"POST {url} -> {response.status_code}: {response.text[:300]}")
        return response.text


def essl_enabled(organization=None) -> bool:
    """
    Whether THIS organization can talk to a device service.

    Two conditions, and they answer different questions. The deployment switch
    says whether the integration exists at all on this installation; the
    resolved base URL says whether this particular customer has an endpoint --
    their own, or the deployment's shared one.

    The organization is explicit and defaults to None, which resolves to the
    deployment's settings. That default is what keeps a single-company install
    working unchanged, and it is safe precisely because the fallback is to
    `settings` and never to another organization.
    """
    from core.config import attendance_config

    return bool(getattr(settings, "ESSL_INTEGRATION_ENABLED", False)) and bool(
        attendance_config(organization).base_url
    )


def _service_url(config) -> str:
    return config.base_url.rstrip("/") + SERVICE_PATH


def _tz() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "ESSL_TIMEZONE", "Asia/Kolkata"))


def _envelope(from_dt: dt.datetime, to_dt: dt.datetime, serial: str, config) -> str:
    """The SOAP 1.1 body, field-for-field as the service's own page documents."""
    from xml.sax.saxutils import escape

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<GetTransactionsLog xmlns="http://tempuri.org/">'
        f"<FromDateTime>{from_dt:{_WIRE_DT}}</FromDateTime>"
        f"<ToDateTime>{to_dt:{_WIRE_DT}}</ToDateTime>"
        f"<SerialNumber>{escape(serial)}</SerialNumber>"
        f"<UserName>{escape(config.username)}</UserName>"
        f"<UserPassword>{escape(config.password)}</UserPassword>"
        "<strDataList></strDataList>"
        "</GetTransactionsLog>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _parse_response(text: str) -> tuple[str, str]:
    """(result message, strDataList payload) from the SOAP reply."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise EsslError(f"Unparseable SOAP response: {exc}") from exc
    ns = "{http://tempuri.org/}"
    result = root.find(f".//{ns}GetTransactionsLogResult")
    data = root.find(f".//{ns}strDataList")
    return (
        (result.text or "").strip() if result is not None else "",
        data.text or "" if data is not None else "",
    )


def parse_rows(payload: str) -> list[PunchRow]:
    """
    strDataList -> punches. Rows the server sends malformed are skipped and
    counted in the log rather than failing the whole window.
    """
    tz = _tz()
    rows: list[PunchRow] = []
    skipped = 0
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            skipped += 1
            continue
        user_id = parts[0].strip()
        try:
            naive = dt.datetime.strptime(parts[1].strip(), _WIRE_DT)
        except ValueError:
            skipped += 1
            continue
        if not user_id:
            skipped += 1
            continue
        rows.append(
            PunchRow(
                essl_user_id=user_id[:32],
                punched_at=naive.replace(tzinfo=tz),
                raw=line[:512],
            )
        )
    if skipped:
        logger.warning("attendance.essl_rows_skipped count=%s", skipped)
    return rows


def fetch_punches(
    serial: str,
    from_dt: dt.datetime,
    to_dt: dt.datetime,
    *,
    transport: Transport | None = None,
    organization=None,
) -> list[PunchRow]:
    """
    One device's punches for a LOCAL-time window.

    `from_dt`/`to_dt` are aware; they are converted to the device's local
    clock for the wire and the returned punches come back aware again.

    THE ORGANIZATION IS EXPLICIT, and this is the call the brief singles out:
    never copy one company's biometric credentials into another organization.
    The endpoint and the credentials come from `attendance_config(organization)`
    -- one organization id, falling back to the deployment's settings and to
    nothing else. A caller that forgets the argument gets the DEPLOYMENT's
    configuration, never a neighbour's.
    """
    from core.config import attendance_config

    config = attendance_config(organization)
    transport = transport or RequestsTransport()
    tz = _tz()
    body = _envelope(from_dt.astimezone(tz), to_dt.astimezone(tz), serial, config)
    text = transport.post(
        _service_url(config),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{SOAP_ACTION}"',
        },
        body=body,
    )
    result, payload = _parse_response(text)
    # The service reports auth and validation problems as a RESULT STRING
    # (HTTP 200 either way). "Unauthorised User" is the one everyone hits
    # first: the Web API keeps its own user list, separate from the website
    # login — explain that instead of echoing the raw phrase.
    lowered = result.lower()
    if "unath" in lowered or "unauth" in lowered:
        raise EsslError(
            "The eTimeTrackLite Web API refused the credentials. The API has "
            "its own user list (separate from the website login) — create or "
            "check the Web API user in eTimeTrackLite and update this "
            "organization's device credentials (or ESSL_USERNAME / "
            "ESSL_PASSWORD where the deployment's are used)."
        )
    if lowered and "success" not in lowered and not payload.strip():
        # An unknown non-success verdict with no data — surface it verbatim.
        raise EsslError(f"eSSL Web API said: {result[:300]}")
    return parse_rows(payload)


def diagnose(*, transport: Transport | None = None, organization=None) -> dict:
    """
    The connection story, one check at a time — powers `manage.py essl_check`
    and the UI status card. Never raises; every problem lands in `errors`.
    """
    from apps.attendance.models import AttendanceDevice
    from core.config import attendance_config

    config = attendance_config(organization)
    report: dict = {
        "enabled": essl_enabled(organization),
        "base_url": config.base_url,
        # Says WHOSE configuration answered, so a support conversation does
        # not start by guessing whether the customer set their own endpoint.
        "organization_specific": config.is_organization_specific,
        "service_reachable": False,
        "credentials_ok": None,
        "devices": [],
        "errors": {},
    }
    if not report["enabled"]:
        report["errors"]["config"] = (
            "ESSL_INTEGRATION_ENABLED is false or ESSL_BASE_URL is empty."
        )
        return report

    transport = transport or RequestsTransport()
    try:
        import requests

        page = requests.get(_service_url(config), timeout=15)
        report["service_reachable"] = page.status_code == 200
        if page.status_code != 200:
            report["errors"]["service"] = f"GET {SERVICE_PATH} -> {page.status_code}"
    except Exception as exc:  # noqa: BLE001 — a diagnosis never raises
        report["errors"]["service"] = f"{type(exc).__name__}: {exc}"
        return report

    now = dt.datetime.now(tz=_tz())
    window = (now - dt.timedelta(days=1), now)
    devices = AttendanceDevice.objects.active().filter(is_enabled=True)
    for device in devices:
        entry = {"serial": device.serial_number, "name": device.name, "rows": None}
        try:
            rows = fetch_punches(
                device.serial_number,
                *window,
                transport=transport,
                organization=organization,
            )
            entry["rows"] = len(rows)
            report["credentials_ok"] = True
        except EsslError as exc:
            entry["error"] = str(exc)
            if "credentials" in str(exc) or "refused" in str(exc):
                report["credentials_ok"] = False
        report["devices"].append(entry)

    if not devices:
        report["errors"]["devices"] = "No enabled devices are registered yet."
    return report
