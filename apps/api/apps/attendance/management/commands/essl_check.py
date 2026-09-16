"""manage.py essl_check — verify the eTimeTrackLite connection end to end."""

from __future__ import annotations

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    """
    One organization's device integration.

    Since per-organization configuration landed, the endpoint and credentials
    answering this check belong to a specific customer -- and `diagnose()` also
    reads that customer's registered devices. Asked without a tenant it reported
    on the deployment's fallback settings while claiming to describe a
    customer's integration, which is the wrong answer given confidently.

    The report says whose configuration answered, so a support conversation does
    not open by guessing. It prints the endpoint and never the credentials.
    """

    help = (
        "Checks the eSSL eTimeTrackLite Web API: configuration, reachability, "
        "credentials, and a one-day probe per registered device."
    )

    def handle_for_organization(self, organization, *args, **options):
        from apps.attendance.services.essl_client import diagnose

        report = diagnose(organization=organization)

        self.stdout.write(f"enabled:            {report['enabled']}")
        self.stdout.write(f"base url:           {report['base_url'] or '(not set)'}")
        self.stdout.write(f"service reachable:  {report['service_reachable']}")
        self.stdout.write(f"credentials ok:     {report['credentials_ok']}")

        for device in report["devices"]:
            if "error" in device:
                self.stdout.write(self.style.ERROR(
                    f"  device {device['serial']} ({device['name']}): {device['error']}"
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"  device {device['serial']} ({device['name']}): "
                    f"{device['rows']} punches in the last 24h"
                ))

        for key, message in report["errors"].items():
            self.stdout.write(self.style.ERROR(f"{key}: {message}"))

        if not report["errors"] and report["credentials_ok"]:
            self.stdout.write(self.style.SUCCESS("eSSL connection verified."))
