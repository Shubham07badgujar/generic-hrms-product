"""
Run the candidate retention policy by hand.

Dry by default, following `repair_application_stages`. A routine whose whole
purpose is to destroy personal data should say what it would destroy before it
destroys it.

    manage.py purge_candidates            # report only
    manage.py purge_candidates --apply    # anonymise

The scheduled task does the same work with `apply=True`; this exists so an
operator can see what is pending, and so the policy can be run out of band after
a bulk import without waiting for the nightly beat.
"""

from __future__ import annotations

from apps.recruitment.services.retention import (
    candidates_due_for_purge,
    purge_expired_candidates,
)
from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = "Anonymise candidate personal data past its retention period."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually anonymise. Without this, nothing is written.",
        )

    def handle_for_organization(self, organization, *args, **options):
        apply = options["apply"]

        due = candidates_due_for_purge()
        total = due.count()

        self.stdout.write(self.style.MIGRATE_HEADING("Candidates past retention"))
        if not total:
            self.stdout.write("  none — nothing is due.")
            return

        # Identifiers and reasons only. Printing the names of people whose data
        # is about to be destroyed would put them in a terminal log and a shell
        # history, which is the opposite of what this command is for.
        for candidate in due.only("id", "consent_given", "retention_until")[:50]:
            clock = (
                f"final decision, due {candidate.retention_until}"
                if candidate.retention_until
                else "unaffirmed bulk import"
            )
            self.stdout.write(f"  {candidate.pk}  ({clock})")
        if total > 50:
            self.stdout.write(f"  … and {total - 50} more")

        if not apply:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"{total} candidate(s) would be anonymised. "
                    f"Nothing has been written. Re-run with --apply."
                )
            )
            return

        result = purge_expired_candidates(apply=True)
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Anonymised {result.count} of {result.considered} considered. "
                f"Hiring records are intact; personal data is gone."
            )
        )
