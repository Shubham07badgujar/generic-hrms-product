"""manage.py seed_attendance — per-location shift rules from the timing chart."""

from __future__ import annotations

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = "Seed the default and per-location attendance shift rules."

    def handle_for_organization(self, organization, *args, **options):
        from apps.attendance.seeds import seed_shift_rules

        count = seed_shift_rules()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {count} shift rule(s) for {organization.slug}."
            )
        )
