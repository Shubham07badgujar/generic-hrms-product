"""Seed the approved hiring workflow configurations."""

from core.management.orgcommand import OrganizationCommand


class Command(OrganizationCommand):
    help = "Create or update the approved hiring workflows (idempotent)."

    def handle_for_organization(self, organization, *args, **options):
        from apps.workflows.seeds import seed_workflows

        for workflow in seed_workflows():
            stages = workflow.stages.count()
            transitions = sum(
                s.outgoing_transitions.count() for s in workflow.stages.all()
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {workflow.name}: {stages} stages, "
                    f"{transitions} transitions"
                )
            )
