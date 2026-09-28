"""
Create a SaaS operator account.

    manage.py bootstrap_platform_admin --email ops@example.com --first-name Asha

The ONLY way `User.is_platform_admin` is ever set. The field is
`editable=False`, so it appears in no form, no serializer and no admin page,
and no HTTP route sets it -- which means platform authority cannot be granted
by anything reachable over the network, including by a platform admin who
already has it.

Deliberately no HTTP twin, unlike `bootstrap_admin`. That one exists because
the approved plan called for a Postman-callable route to create a customer's
first Admin; this creates the operator of the entire deployment, and the right
number of network-reachable ways to do that is zero.
"""

from __future__ import annotations

from django.core.management.base import CommandError

from core.access.platform_command import PlatformCommand
from django.db import transaction

from apps.accounts.models import User


class Command(PlatformCommand):
    #: Deployment-level work: see core/access/platform_command.py.
    platform_reason = "grant or revoke a platform operator"

    help = "Create or promote a Platform Admin (the SaaS operator)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument(
            "--revoke",
            action="store_true",
            help="Remove platform authority from an existing account instead.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        user = User.objects.filter(email=email).first()

        if options["revoke"]:
            if user is None:
                raise CommandError(f"No account for {email}.")
            if not user.is_platform_admin:
                raise CommandError(f"{email} is not a platform admin.")
            user.is_platform_admin = False
            user.save(update_fields=["is_platform_admin"])
            self.stdout.write(self.style.SUCCESS(f"\n  Revoked: {email}\n"))
            return

        if user is None:
            user = User.objects.create_user(
                email=email,
                password=None,  # unusable until set out of band, below
                first_name=options["first_name"],
                last_name=options["last_name"],
            )
            created = True
        else:
            created = False
            # Refusing to promote an account that belongs to a customer is the
            # point of this branch. One person holding both an organization
            # membership and platform authority would be the single principal
            # the whole domain split exists to prevent -- and they would not
            # even work, because `resolve_context` short-circuits to the
            # platform context and their organization would go dark.
            if user.memberships.exists():
                raise CommandError(
                    f"{email} belongs to an organization. A platform operator "
                    f"must not also be a customer's user: the two domains are "
                    f"disjoint, and promoting this account would silently cut "
                    f"off its access to its own organization. Use a separate "
                    f"address."
                )
            if user.is_platform_admin:
                raise CommandError(f"{email} is already a platform admin.")

        user.is_platform_admin = True
        user.save(update_fields=["is_platform_admin"])

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  Platform admin {'created' if created else 'promoted'}: "
                f"{user.email}"
            )
        )
        self.stdout.write(f"  User id: {user.pk}\n")
        if created:
            self.stdout.write(
                self.style.WARNING(
                    "  This account has NO usable password. Set one out of band:\n\n"
                    f"      manage.py changepassword {user.email}\n\n"
                    "  Then sign in at /api/v1/auth/login/platform/. This account\n"
                    "  holds no role in any organization and reaches no customer\n"
                    "  HR data -- that is not a setting, it is what the account is.\n"
                )
            )
