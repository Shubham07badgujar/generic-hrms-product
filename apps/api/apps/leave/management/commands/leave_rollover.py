"""manage.py leave_rollover --year 2027 — carry balances into the new year."""
from django.utils import timezone

from apps.leave.services import annual_rollover
from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    """
    One organization's balances, under that organization's leave policies.

    Carry-forward limits are per policy and policies are per customer, so there
    is no such thing as rolling over "everyone". Running unbound was harmless
    only while the manager did not filter; it becomes an `OrgContextMissing` the
    moment leave does.
    """

    help = "Carry forward last year's remaining balances, per policy."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--year", type=int, default=timezone.localdate().year)

    def handle_for_organization(self, organization, *args, **options):
        moved = annual_rollover(year=options["year"])
        self.stdout.write(self.style.SUCCESS(f"Carried forward {moved} balance(s)."))
