"""Seed the approved hiring workflow configurations."""

from django.core.management.base import BaseCommand

from apps.workflows.seeds import seed_workflows


class Command(BaseCommand):
    help = "Create or update the approved hiring workflows (idempotent)."

    def handle(self, *args, **options):
        workflows = seed_workflows()
        for workflow in workflows:
            stages = workflow.stages.count()
            transitions = sum(s.outgoing_transitions.count() for s in workflow.stages.all())
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {workflow.name}: {stages} stages, {transitions} transitions"
                )
            )
