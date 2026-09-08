"""
Load the statutory rate-set fixtures into the database.

Every row lands as DRAFT. The command CANNOT mark anything verified, and that
is the point: verification is a human act performed by the Finance Head against
a gazette source, recorded with their name against it. A seeder that could
produce verified rates would make the whole verification workflow decorative,
and payroll would once again run on figures nobody checked.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.statutory.models import StatutoryRuleSet, VerificationStatus

FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "fixtures" / "statutory" / "rate_sets"


class Command(BaseCommand):
    help = "Load statutory rate sets from fixtures/statutory/rate_sets as DRAFT rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Overwrite parameters on rows that already exist and are not verified.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not FIXTURE_ROOT.exists():
            self.stderr.write(f"No fixtures at {FIXTURE_ROOT}")
            return

        created = updated = skipped = 0

        for path in sorted(FIXTURE_ROOT.rglob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            key = {
                "statute": raw["statute"],
                "jurisdiction": raw.get("jurisdiction") or "",
                "regime": raw.get("regime") or "",
                "effective_from": raw["effective_from"],
            }
            existing = StatutoryRuleSet.objects.filter(**key).first()

            if existing and not options["replace"]:
                skipped += 1
                continue
            if existing and existing.verification_status == VerificationStatus.VERIFIED:
                # Overwriting a verified rate set would silently un-verify it on
                # the next save. Refuse, and say so — this is exactly the kind of
                # quiet change the tamper check exists to catch.
                self.stdout.write(self.style.WARNING(
                    f"  skipped {path.name}: already VERIFIED. Supersede it with a new "
                    f"effective-dated row rather than editing it in place."
                ))
                skipped += 1
                continue

            source = raw.get("source") or {}
            fields = {
                "financial_year": raw.get("financial_year") or "",
                "effective_to": raw.get("effective_to"),
                "rule_version": raw["rule_version"],
                "parameters": raw.get("parameters") or {},
                "source_citation": (source.get("citation") or "").strip()[:500],
                "source_url": source.get("url") or "",
                "retrieved_on": source.get("retrieved_on"),
                "assumptions": raw.get("assumptions") or [],
                "verification_status": VerificationStatus.DRAFT,
            }

            if existing:
                for name, value in fields.items():
                    setattr(existing, name, value)
                existing.save()
                updated += 1
            else:
                StatutoryRuleSet.objects.create(**key, **fields)
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Statutory rate sets: {created} created, {updated} updated, {skipped} skipped."
        ))
        self.stdout.write(
            "  All rows are DRAFT. Payroll can be PROCESSED against them so the figures\n"
            "  are visible, but no run can be APPROVED until the Finance Head verifies\n"
            "  each rate set against its gazette source."
        )
