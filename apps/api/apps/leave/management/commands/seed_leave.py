"""manage.py seed_leave — default leave types, policies and calendar."""
from django.core.management.base import BaseCommand

from apps.leave.seeds import seed_leave


class Command(BaseCommand):
    help = "Seed default leave types, policies and the standard calendar."

    def handle(self, *args, **options):
        result = seed_leave()
        self.stdout.write(self.style.SUCCESS(f"Seeded: {result}"))
