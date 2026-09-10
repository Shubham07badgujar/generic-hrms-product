"""Seed the default exit clearance template. Idempotent."""

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = "Seed the default exit clearance template and its items."

    def handle_for_organization(self, organization, *args, **options):
        from apps.offboarding.seeds import seed_all

        counts = seed_all()
        summary = " | ".join(f"{key}: {value}" for key, value in counts.items())
        self.stdout.write(
            self.style.SUCCESS(f"Seeded for {organization.slug}. {summary}")
        )
