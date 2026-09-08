"""
Create the founding Admin account.

    manage.py bootstrap_admin --email admin@example.com --first-name Asha

The RECOMMENDED path — zero network surface, no token to leak, no endpoint to
leave enabled by accident. The HTTP endpoint exists only because the approved
plan calls for a Postman-callable route; prefer this.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.services.bootstrap import BootstrapError, create_admin


class Command(BaseCommand):
    help = "Create the founding Admin account (one-time)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    def handle(self, *args, **options):
        try:
            user = create_admin(
                email=options["email"],
                first_name=options["first_name"],
                last_name=options["last_name"],
            )
        except BootstrapError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"\n  Admin created: {user.email}"))
        self.stdout.write(f"  User id: {user.pk}\n")
        self.stdout.write(
            self.style.WARNING(
                "  This account has NO usable password. Set one out of band:\n\n"
                f"      manage.py changepassword {user.email}\n\n"
                "  Then sign in at /login/admin. Once done, unset\n"
                "  ADMIN_BOOTSTRAP_TOKEN so the HTTP bootstrap route disappears.\n"
            )
        )
