"""Seed onboarding, document, letter and asset configuration. Idempotent."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed default document types, onboarding template, letters and asset categories."

    def handle(self, *args, **options):
        from apps.onboarding.seeds import seed_all

        counts = seed_all()
        summary = " | ".join(f"{key}: {value}" for key, value in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Seeded. {summary}"))
