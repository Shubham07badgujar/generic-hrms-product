"""
Seed the canonical roles and permission matrix.

    manage.py seed_roles           # apply
    manage.py seed_roles --dump    # print the matrix for review, change nothing
    manage.py seed_roles --csv out.csv
"""

from __future__ import annotations

import csv
from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.accounts.permission_matrix import ROLE_SPECS, cell_count
from apps.accounts.services.roles import matrix_report, seed_roles
from core.access.catalog import Scope

SCOPE_SYMBOL = {
    "NONE": "-",
    "SELF": "S",
    "TEAM": "T",
    "DEPARTMENT": "D",
    "ALL": "A",
}


class Command(BaseCommand):
    help = "Create or update the 18 canonical roles and their permission matrix."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dump",
            action="store_true",
            help="Print the matrix without writing anything.",
        )
        parser.add_argument("--csv", metavar="PATH", help="Write the matrix to CSV.")
        parser.add_argument(
            "--no-prune",
            action="store_true",
            help="Keep permission rows no longer present in the matrix.",
        )

    def handle(self, *args, **options):
        if options["dump"]:
            self._dump_spec()
            return

        result = seed_roles(prune=not options["no_prune"])
        self.stdout.write(self.style.SUCCESS(f"Seeded. {result}"))

        if options["csv"]:
            self._write_csv(options["csv"])

    # -- output helpers ----------------------------------------------------

    def _dump_spec(self):
        """Print the declared matrix straight from the spec, before any DB write."""
        self.stdout.write(self.style.MIGRATE_HEADING("\nROLES"))
        self.stdout.write(
            f"  {'code':<20} {'name':<28} {'L':<3} {'flags':<24} dashboard"
        )
        self.stdout.write("  " + "-" * 92)
        for spec in ROLE_SPECS:
            flags = []
            if spec.is_read_only:
                flags.append("read-only")
            if spec.can_manage_users:
                flags.append("users")
            if not spec.requires_employee:
                flags.append("no-emp")
            if not spec.is_grantable:
                flags.append("bootstrap")
            self.stdout.write(
                f"  {spec.code:<20} {spec.name:<28} {spec.layer:<3} "
                f"{','.join(flags) or '-':<24} {spec.dashboard_key}"
            )

        self.stdout.write(self.style.MIGRATE_HEADING("\nPERMISSIONS BY ROLE"))
        self.stdout.write("  scope: A=all  D=department  T=team  S=self\n")
        for spec in ROLE_SPECS:
            self.stdout.write(self.style.HTTP_INFO(f"\n  {spec.name}  ({spec.code})"))
            grouped = defaultdict(list)
            for resource, actions in sorted(spec.permissions.items()):
                for action, scope in sorted(actions.items()):
                    grouped[str(resource)].append(
                        f"{action}:{SCOPE_SYMBOL[Scope(scope).name]}"
                    )
            for resource, cells in sorted(grouped.items()):
                self.stdout.write(f"    {resource:<24} {'  '.join(cells)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  {len(ROLE_SPECS)} roles, {cell_count()} permission cells. "
                f"Nothing written (--dump).\n"
            )
        )

    def _write_csv(self, path: str):
        rows = matrix_report()
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["role", "role_code", "layer", "resource", "action", "scope", "customized"],
            )
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} rows to {path}"))
