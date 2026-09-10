"""Seed onboarding, document, letter and asset configuration. Idempotent."""

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = (
        "Seed default document types, onboarding template, letters and "
        "asset categories."
    )

    def handle_for_organization(self, organization, *args, **options):
        from apps.onboarding.seeds import seed_all

        counts = seed_all()
        summary = " | ".join(f"{key}: {value}" for key, value in counts.items())
        self.stdout.write(
            self.style.SUCCESS(f"Seeded for {organization.slug}. {summary}")
        )
