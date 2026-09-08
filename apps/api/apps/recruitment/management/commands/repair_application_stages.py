"""
Repair applications whose status and stage disagree.

The override used to change `status` without moving `current_stage`, leaving
rows that read as live while sitting at a terminal stage with no decisions
available. The service no longer produces that state, but rows created before
the fix keep it — a code fix does not reach back through the database.

Runs the same engine rule the override now uses, so a repaired row lands
exactly where a fresh override would have put it. Dry by default: it reports
what it would change and writes nothing until `--apply` is passed, because a
command that silently moves candidates through a hiring pipeline is not one
anybody should run to "see what happens".
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Realign applications whose status and current_stage disagree."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        from apps.recruitment.models import Application, ApplicationEvent
        from apps.recruitment.services.engine import (
            CLOSED_STATUSES,
            WorkflowError,
            align_stage_with_status,
        )

        applications = Application.objects.select_related(
            "current_stage", "job_opening__workflow", "candidate"
        ).all()

        incoherent = []
        for application in applications:
            stage = application.current_stage
            closing = application.status in CLOSED_STATUSES
            if closing != stage.is_terminal:
                incoherent.append(application)

        if not incoherent:
            self.stdout.write(self.style.SUCCESS("No incoherent applications found."))
            return

        self.stdout.write(f"Found {len(incoherent)} application(s) to realign:\n")
        repaired = 0
        failed = 0

        for application in incoherent:
            label = f"{application.candidate.full_name} · {application.job_opening.title}"
            try:
                target = align_stage_with_status(application, new_status=application.status)
            except WorkflowError as exc:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  FAILED {label}: cannot resolve a stage — {exc.messages[0]}"
                    )
                )
                continue

            self.stdout.write(
                f"  {label}: status={application.status} "
                f"'{application.current_stage.name}' -> '{target.name}'"
            )

            if not options["apply"]:
                continue

            with transaction.atomic():
                previous = application.current_stage
                application.current_stage = target
                application.save(update_fields=["current_stage", "updated_at"])
                # Recorded like any other movement, so the repair is visible in
                # the candidate's history rather than appearing to have always
                # been this way.
                ApplicationEvent.objects.create(
                    application=application,
                    kind=ApplicationEvent.Kind.STAGE_CHANGED,
                    actor=None,
                    actor_label="system · stage repair",
                    from_stage=previous,
                    to_stage=target,
                    note=(
                        "Realigned by repair_application_stages: the status and stage "
                        "disagreed, which predates the override stage-alignment fix."
                    ),
                    detail={"repair": True, "status": application.status},
                )
            repaired += 1

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(f"\nRepaired {repaired}; {failed} could not be resolved.")
            )
        else:
            self.stdout.write(
                self.style.WARNING("\nDry run — nothing written. Re-run with --apply.")
            )
