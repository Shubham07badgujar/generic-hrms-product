"""manage.py essl_check — verify the eTimeTrackLite connection end to end."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Checks the eSSL eTimeTrackLite Web API: configuration, reachability, "
        "credentials, and a one-day probe per registered device."
    )

    def handle(self, *args, **options):
        from apps.attendance.services.essl_client import diagnose

        report = diagnose()

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
