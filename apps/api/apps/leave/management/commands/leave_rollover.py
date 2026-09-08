"""manage.py leave_rollover --year 2027 — carry balances into the new year."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.leave.services import annual_rollover


class Command(BaseCommand):
    help = "Carry forward last year's remaining balances, per policy."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=timezone.localdate().year)

    def handle(self, *args, **options):
        moved = annual_rollover(year=options["year"])
        self.stdout.write(self.style.SUCCESS(f"Carried forward {moved} balance(s)."))
