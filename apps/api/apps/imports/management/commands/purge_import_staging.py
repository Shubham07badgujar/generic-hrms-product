"""
Strip personal data from expired import staging rows.

Dry by default, following `repair_application_stages` and `purge_candidates`.

    manage.py purge_import_staging            # report only
    manage.py purge_import_staging --apply    # destroy the PII

The scheduled task does the same work nightly; this exists so an operator can
see what is pending, and so the policy can be run out of band after a large
import without waiting for beat.
"""

from __future__ import annotations

from apps.imports.services.retention import (
    COMMITTED_DAYS,
    UNCOMMITTED_HOURS,
    purge_staging_pii,
)
from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    """
    One organization's staging rows, never everybody's.

    Retention is per customer: their rows, their clock, their audit trail. The
    command took no organization at all, which was invisible while the manager
    did not filter and becomes an `OrgContextMissing` the moment imports does.
    The base class supplies `--organization`, and needs it only once a
    deployment has more than one company.

    The nightly sweep is the Celery dispatcher, which fans out across every
    running organization. This exists for the operator who wants to see what is
    pending, or to run the policy out of band after a large import.
    """

    help = "Remove personal data from candidate-import staging rows past retention."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually destroy the staging PII. Without this, nothing is written.",
        )

    def handle_for_organization(self, organization, *args, **options):
        apply = options["apply"]
        result = purge_staging_pii(apply=apply)

        self.stdout.write(self.style.MIGRATE_HEADING("Import staging PII"))
        self.stdout.write(
            f"  uncommitted, older than {UNCOMMITTED_HOURS}h : "
            f"{result.uncommitted_rows} row(s)"
        )
        self.stdout.write(
            f"  committed, older than {COMMITTED_DAYS}d     : "
            f"{result.committed_rows} row(s)"
        )
        self.stdout.write(f"  across {result.batches} batch(es)")
        self.stdout.write("")

        if not result.total:
            self.stdout.write(self.style.SUCCESS("Nothing is due."))
            return

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"{result.total} row(s) would be stripped of personal data. "
                    f"Batch counts, hashes, outcomes and audit records are kept. "
                    f"Nothing has been written — re-run with --apply."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Stripped {result.total} row(s). The candidates and applications "
                f"these imports created are untouched."
            )
        )
