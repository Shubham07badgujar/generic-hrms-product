"""
Create a customer organization from the command line.

    manage.py provision_organization --name "Company A" \
        --admin-email admin@company-a.example --city Pune --state MH

The service this wraps has existed and been tested since the platform layer
landed, and was reachable from nothing but the test suite: the console lists
organizations read-only, and there was no command. So onboarding a customer
meant opening a Django shell and calling a function by hand, which is not a
product feature -- it is a workaround that happens to work.

DELIBERATELY NOT AN `OrganizationCommand`. Every other operator command acts on
an organization and refuses to guess which one; this one CREATES one, so
requiring an existing organization would be circular. It is a platform action,
like `bootstrap_platform_admin` and `seed_plans`.

THE TEMPORARY PASSWORD is printed only when the invitation email did not go
out. Mail is the intended channel and the one that does not leave the
credential in a terminal scrollback or a shell history; but a fresh deployment
often has no SMTP configured yet, and an administrator who cannot be told their
password cannot sign in to a company that was just created for them. So it is
the fallback, announced as one, rather than the default.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a customer organization, its configuration and its first administrator."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Display name of the company.")
        parser.add_argument(
            "--admin-email",
            required=True,
            help=(
                "The first administrator's login. A login belongs to exactly "
                "one organization, so an address already in use is refused."
            ),
        )
        parser.add_argument(
            "--slug",
            default="",
            help="URL slug. Derived from the name when omitted; must be unique.",
        )
        parser.add_argument("--legal-name", default="")
        parser.add_argument("--admin-first-name", default="")
        parser.add_argument("--admin-last-name", default="")
        parser.add_argument("--primary-email", default="")
        parser.add_argument("--phone", default="")
        parser.add_argument("--city", default="")
        parser.add_argument("--state", default="")
        parser.add_argument(
            "--country", default="", help="ISO 3166-1 alpha-2 code, not a country name."
        )
        parser.add_argument("--timezone", default="")
        parser.add_argument("--currency", default="")
        parser.add_argument(
            "--plan",
            default="",
            help=(
                "Plan code to start on. Omitted, the cheapest public plan is "
                "used, or none at all where the deployment sells nothing."
            ),
        )

    def handle(self, *args, **options):
        from apps.platform.models import Plan
        from apps.platform.services.provisioning import (
            ProvisioningError,
            provision_organization,
        )

        plan = None
        if options["plan"]:
            plan = Plan.objects.filter(code=options["plan"], is_active=True).first()
            if plan is None:
                raise CommandError(f"No active plan with code {options['plan']!r}.")

        try:
            result = provision_organization(
                name=options["name"],
                slug=options["slug"],
                legal_name=options["legal_name"],
                admin_email=options["admin_email"],
                admin_first_name=options["admin_first_name"],
                admin_last_name=options["admin_last_name"],
                primary_email=options["primary_email"],
                phone=options["phone"],
                city=options["city"],
                state=options["state"],
                country=options["country"],
                timezone_name=options["timezone"],
                currency=options["currency"],
                plan=plan,
            )
        except ProvisioningError as exc:
            # A refusal the operator can act on, raised before the first write.
            # `CommandError` rather than a traceback, because "that slug is
            # taken" is an answer, not a crash.
            raise CommandError(str(exc)) from exc

        organization = result.organization
        self.stdout.write(
            self.style.SUCCESS(f"\n  {organization.name} provisioned.")
        )
        self.stdout.write(f"  Slug:          {organization.slug}")
        self.stdout.write(f"  Status:        {organization.status}")
        self.stdout.write(f"  Administrator: {result.admin.email}")
        if result.subscription is not None:
            self.stdout.write(f"  Plan:          {result.subscription.plan.code}")
        for key, outcome in result.seeded.items():
            self.stdout.write(f"    {key:<14} {outcome}")

        self.stdout.write("")
        if result.invitation_sent:
            self.stdout.write(
                f"  An invitation with sign-in instructions went to "
                f"{result.admin.email}."
            )
        else:
            # Not a failure of provisioning: the organization is complete and
            # correct. But nobody can sign in to it until this reaches the
            # administrator, so it is said loudly rather than logged quietly.
            self.stdout.write(
                self.style.WARNING(
                    "  The invitation email did NOT go out -- mail is not "
                    "configured, or the send failed."
                )
            )
            self.stdout.write(
                f"  Temporary password: {result.temporary_password}"
            )
            self.stdout.write(
                "  Give it to the administrator over a channel you trust. It "
                "is not stored anywhere and cannot be looked up; if it is "
                "lost, set a new one with `manage.py changepassword`."
            )

        self.stdout.write(
            "\n  They must change it at first sign-in, then walk the setup "
            "wizard. The organization becomes ACTIVE when they finish it.\n"
        )
