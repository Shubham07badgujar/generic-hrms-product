"""manage.py seed_attendance — per-location shift rules from the timing chart."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed the default and per-location attendance shift rules."

    def handle(self, *args, **options):
        from apps.attendance.seeds import seed_shift_rules

        count = seed_shift_rules()
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} shift rule(s)."))
