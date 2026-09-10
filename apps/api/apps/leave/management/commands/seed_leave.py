"""manage.py seed_leave — default leave types, policies and calendar."""

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = "Seed default leave types, policies and the standard calendar."

    def handle_for_organization(self, organization, *args, **options):
        from apps.leave.seeds import seed_leave

        result = seed_leave()
        self.stdout.write(
            self.style.SUCCESS(f"Seeded for {organization.slug}: {result}")
        )
