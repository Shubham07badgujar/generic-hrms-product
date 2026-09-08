"""
Clear `must_change_password` on accounts that were flagged before the flag
was enforced.

WHY THIS EXISTS
---------------
`must_change_password` was set at account creation long before anything acted
on it, so accounts seeded during setup carry it without ever having been asked
to change anything. Enforcement arrives with `PasswordChangeRequired`; the
moment it does, those accounts can reach nothing but the change screen, even
though their owners have been signing in with settled passwords for weeks.

Clearing the flag for those PRE-EXISTING accounts is the correction. Accounts
created from now on keep the flag and the forced first-login change — that is
the feature, and this command never touches it, because it only clears
accounts you name or that already existed before a cut-off you give it.

DRY BY DEFAULT, like `repair_application_stages`. It prints what it would do
and changes nothing until `--apply`.

REVERSIBLE. Every run with `--apply` writes a manifest of exactly which user
ids were changed, and `--rollback <manifest>` puts the flag back on precisely
those accounts and no others.

AUDITED. Each change writes an AuditLog row against the User, naming the actor
and both the before and after value — the same trail any other change to an
account produces.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Clear must_change_password on pre-existing accounts. Dry by default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write. Without this the command only reports.",
        )
        parser.add_argument(
            "--created-before",
            default=None,
            help=(
                "Only accounts created strictly before this ISO timestamp "
                "(e.g. 2026-08-18T00:00:00Z). Omit to consider every flagged "
                "account, which is what you want on a first run."
            ),
        )
        parser.add_argument(
            "--email",
            action="append",
            default=[],
            help="Limit to these addresses. Repeatable. Overrides --created-before.",
        )
        parser.add_argument(
            "--manifest",
            default="/tmp/password_flag_manifest.json",
            help="Where to write (or read, with --rollback) the list of changed ids.",
        )
        parser.add_argument(
            "--rollback",
            action="store_true",
            help="Restore the flag on exactly the accounts named in --manifest.",
        )
        parser.add_argument(
            "--actor",
            default=None,
            help="Email of the administrator this change is attributed to in the audit log.",
        )

    def handle(self, *args, **options):
        from apps.accounts.models import User

        manifest_path = Path(options["manifest"])

        if options["rollback"]:
            return self._rollback(manifest_path, options)

        queryset = User.objects.filter(must_change_password=True, is_active=True)
        if options["email"]:
            queryset = queryset.filter(email__in=options["email"])
        elif options["created_before"]:
            cutoff = timezone.datetime.fromisoformat(
                options["created_before"].replace("Z", "+00:00")
            )
            queryset = queryset.filter(date_joined__lt=cutoff)

        targets = list(queryset.order_by("email"))
        if not targets:
            self.stdout.write("Nothing to do: no active account carries the flag.")
            return

        self.stdout.write(f"{len(targets)} account(s) currently flagged:")
        for user in targets:
            stamp = getattr(user, "last_login_at", None) or user.last_login
            last = stamp.isoformat() if stamp else "never signed in"
            self.stdout.write(
                f"  {user.email:46} joined {user.date_joined:%Y-%m-%d}  last login: {last}"
            )

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nDRY RUN — nothing written. Re-run with --apply to clear these."
                )
            )
            return

        actor = self._actor(options)
        with transaction.atomic():
            ids = [str(user.pk) for user in targets]
            for user in targets:
                user.must_change_password = False
                user.save(update_fields=["must_change_password"])
                self._audit(user, actor=actor, before=True, after=False)

            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "cleared_at": timezone.now().isoformat(),
                        "actor": getattr(actor, "email", None),
                        "user_ids": ids,
                        "emails": [user.email for user in targets],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nCleared {len(targets)} account(s). Manifest: {manifest_path}\n"
                f"To undo:  manage.py clear_password_change_flag --rollback "
                f"--manifest {manifest_path} --apply"
            )
        )

    # ------------------------------------------------------------ rollback

    def _rollback(self, manifest_path: Path, options):
        from apps.accounts.models import User

        if not manifest_path.exists():
            raise CommandError(f"No manifest at {manifest_path}.")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ids = manifest.get("user_ids", [])
        targets = list(User.objects.filter(pk__in=ids).order_by("email"))

        self.stdout.write(
            f"Manifest from {manifest.get('cleared_at')} names {len(ids)} account(s); "
            f"{len(targets)} still exist:"
        )
        for user in targets:
            self.stdout.write(f"  {user.email:46} flag is currently {user.must_change_password}")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("\nDRY RUN — nothing written. Add --apply to restore the flag.")
            )
            return

        actor = self._actor(options)
        with transaction.atomic():
            for user in targets:
                user.must_change_password = True
                user.save(update_fields=["must_change_password"])
                self._audit(user, actor=actor, before=False, after=True)

        self.stdout.write(self.style.SUCCESS(f"\nRestored the flag on {len(targets)} account(s)."))

    # ------------------------------------------------------------- helpers

    def _actor(self, options):
        from apps.accounts.models import User

        if options["actor"]:
            actor = User.objects.filter(email=options["actor"]).first()
            if actor is None:
                raise CommandError(f"No user with email {options['actor']}.")
            return actor
        return None

    def _audit(self, user, *, actor, before: bool, after: bool) -> None:
        from apps.audit.events import record_event
        from core.access import Resource

        record_event(
            user,
            actor=actor,
            entity_type="accounts.User",
            verb="update",
            resource=Resource.USER,
            before={"must_change_password": before},
            after={
                "event": "password_change_flag_cleared" if not after else "password_change_flag_restored",
                "must_change_password": after,
                "email": user.email,
                "note": (
                    "Flag predates enforcement; the account has a settled password."
                    if not after
                    else "Restored from a manifest."
                ),
            },
        )
