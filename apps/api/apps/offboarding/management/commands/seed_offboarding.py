"""Seed the default exit clearance template. Idempotent."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed the default exit clearance template and its items."

    def handle(self, *args, **options):
        from apps.offboarding.seeds import seed_all

        counts = seed_all()
        summary = " | ".join(f"{key}: {value}" for key, value in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Seeded. {summary}"))
