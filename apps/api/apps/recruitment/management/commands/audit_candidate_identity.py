"""
Report — and optionally repair — candidate identity before migration 0009.

Dry by default. `--apply` retires duplicates that are provably empty, and
nothing else. A command that silently merges two people's hiring histories is
not one anybody should run to see what happens.

Run this BEFORE deploying the identity migrations. 0008 refuses to proceed while
two active candidates share an address case-insensitively, and this is what
tells you which ones and what can be done about them.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count

from apps.recruitment.models import Candidate
from core.management.orgcommand import OrganizationCommand
from core.phone import to_e164_in


def _normalized(candidate) -> str | None:
    return (candidate.email or "").strip().lower() or None


def _is_empty(candidate) -> bool:
    """
    Safe to retire: carries nothing anyone could lose.

    Deliberately strict. A candidate with any application, offer, rejection or
    résumé is somebody's work, and this command will not touch it whatever the
    duplicate looks like.
    """
    return not any(
        (
            candidate.applications.exists(),
            candidate.resume,
            getattr(candidate, "rejections", None) and candidate.rejections.exists(),
        )
    )


class Command(OrganizationCommand):
    help = "Report candidate identity problems that block the 0009 constraints."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Retire duplicates that carry no applications, offers or files.",
        )

    def handle_for_organization(self, organization, *args, **options):
        apply = options["apply"]
        problems = 0

        # 1. Email collisions — the blocker for 0009.
        collisions = (
            Candidate.objects.filter(is_active=True)
            .exclude(email__isnull=True)
            .exclude(email="")
            .values_list("id", "email")
        )
        groups: dict[str, list] = {}
        for pk, email in collisions:
            groups.setdefault(email.strip().lower(), []).append(pk)
        duplicates = {key: ids for key, ids in groups.items() if len(ids) > 1}

        self.stdout.write(self.style.MIGRATE_HEADING("Email collisions"))
        if not duplicates:
            self.stdout.write("  none — the case-insensitive index can be built.")
        for address, ids in duplicates.items():
            problems += 1
            rows = list(Candidate.objects.filter(pk__in=ids))
            empty = [c for c in rows if _is_empty(c)]
            keepers = [c for c in rows if not _is_empty(c)]
            self.stdout.write(f"  {address}: {len(rows)} active rows")
            for row in rows:
                marker = "empty" if row in empty else "HAS HISTORY"
                self.stdout.write(f"    {row.pk}  {row.full_name}  [{marker}]")

            if len(keepers) > 1:
                self.stdout.write(
                    self.style.ERROR(
                        "    -> two or more of these carry hiring history. "
                        "A human must decide; this command will not choose."
                    )
                )
                continue
            if not empty:
                continue

            # Keep the one with history, or the oldest if none has any.
            survivor = keepers[0] if keepers else min(rows, key=lambda c: c.created_at)
            doomed = [c for c in empty if c.pk != survivor.pk]
            if not doomed:
                continue

            if apply:
                with transaction.atomic():
                    for row in doomed:
                        row.is_active = False
                        row.save(update_fields=["is_active", "updated_at"])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    -> retired {len(doomed)}, kept {survivor.pk}"
                    )
                )
            else:
                self.stdout.write(
                    f"    -> would retire {len(doomed)}, keep {survivor.pk}"
                )

        # 2. Rows the backfill will convert, reported so the number is expected.
        blank_email = Candidate.objects.filter(email="").count()
        if blank_email:
            self.stdout.write(
                self.style.MIGRATE_HEADING("\nEmpty-string emails -> NULL")
            )
            self.stdout.write(f"  {blank_email} row(s)")

        # 3. Phones that will not normalize. Not a blocker — they simply drop
        # out of phone matching — but the count should not be a surprise.
        unparseable = sum(
            1
            for phone in Candidate.objects.exclude(phone="").values_list(
                "phone", flat=True
            )
            if to_e164_in(phone) is None
        )
        self.stdout.write(self.style.MIGRATE_HEADING("\nPhone numbers"))
        self.stdout.write(
            f"  {unparseable} will not normalize and will not participate in "
            f"phone matching."
        )

        # 4. Rows that will be labelled legacy_unrecorded.
        no_basis = Candidate.objects.filter(consent_given=False, legal_basis="").count()
        self.stdout.write(self.style.MIGRATE_HEADING("\nLawful basis"))
        self.stdout.write(
            f"  {no_basis} row(s) have no recorded consent and will be marked "
            f"'legacy_unrecorded' by the backfill."
        )

        self.stdout.write("")
        if problems and not apply:
            self.stdout.write(
                self.style.WARNING(
                    f"{problems} collision group(s) block migration 0009. "
                    f"Re-run with --apply to retire the empty duplicates."
                )
            )
        elif problems and apply:
            self.stdout.write(self.style.SUCCESS("Repairs applied. Re-run to confirm."))
        else:
            self.stdout.write(self.style.SUCCESS("Identity is ready for 0009."))
