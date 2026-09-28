"""
Permanently delete an archived organization's data.

    manage.py purge_organization northwind --dry-run
    manage.py purge_organization northwind --confirm northwind

THE ONLY DOOR. There is deliberately no API route and no console button for
this: it is the single irreversible operation in the product, and it happens
on the server, by a person who typed the organization's slug twice. Archiving
is the console's; purging is not.

Refused unless the organization has been ARCHIVED for at least a year. Run it
with `--dry-run` first -- it prints exactly what would go, counted per model,
without writing anything.

See `apps/platform/services/lifecycle.py` for what is removed, what is kept
(a tombstone row, the subscription, and the audit trail with its payloads
scrubbed), and why a login that certified a deployment-wide statutory rate
set is deactivated rather than deleted.
"""

from __future__ import annotations

from django.core.management.base import CommandError

from core.access.platform_command import PlatformCommand

from apps.platform.services.lifecycle import (
    LifecycleError,
    purge_refusal,
    plan_purge,
    purge_organization,
)


class Command(PlatformCommand):
    #: Deployment-level work: see core/access/platform_command.py.
    platform_reason = "purge an archived organization"

    help = "Permanently delete an archived organization's data (irreversible)."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="The organization to purge.")
        parser.add_argument(
            "--confirm", default="",
            help="Type the slug again. Required unless --dry-run.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be removed; write nothing.",
        )

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.organization.models import Organization

        organization = Organization.objects.filter(slug=options["slug"]).first()
        if organization is None:
            raise CommandError(f"No organization with slug {options['slug']!r}.")

        refusal = purge_refusal(organization)
        plan = plan_purge(organization)
        self._report(plan, refusal)

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run: nothing was written."))
            return
        if refusal:
            raise CommandError(refusal)
        if not options["confirm"]:
            raise CommandError(
                "Purging is irreversible. Re-run with --confirm "
                f"{organization.slug} to proceed, or --dry-run to look first."
            )

        # Attributed to an operator when there is exactly one to attribute it
        # to; otherwise the terminal audit row says "no actor", which is true.
        operators = list(User.objects.filter(is_platform_admin=True, is_active=True)[:2])
        actor = operators[0] if len(operators) == 1 else None

        try:
            purge_organization(organization, confirm=options["confirm"], actor=actor)
        except LifecycleError as refusal_raised:
            raise CommandError(str(refusal_raised)) from refusal_raised

        self.stdout.write(self.style.SUCCESS(
            f"\nPurged {organization.slug}: {plan.total_rows} rows, "
            f"{len(plan.users_deleted)} logins. The organization row remains as a "
            f"tombstone and its audit trail remains, scrubbed."
        ))

    def _report(self, plan, refusal):
        organization = plan.organization
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{organization.name} ({organization.slug}) -- {organization.get_status_display()}"
        ))
        if refusal:
            self.stdout.write(self.style.ERROR(f"  NOT ELIGIBLE: {refusal}"))
        self.stdout.write(f"  rows to delete: {plan.total_rows}")
        for label, count in sorted(plan.rows.items()):
            self.stdout.write(f"    {label:<45} {count}")
        self.stdout.write(f"  logins to delete: {len(plan.users_deleted)}")
        if plan.users_spared:
            self.stdout.write(
                f"  logins to deactivate instead (they certified a deployment-wide "
                f"statutory rate set): {', '.join(plan.users_spared)}"
            )
        self.stdout.write(f"  audit rows to scrub (kept): {plan.audit_rows}")
        self.stdout.write(f"  files: {plan.media_path}")
