"""
Existing workflows: verification belongs to the Recruiter, and may reject.

Every active HR_VERIFICATION stage — in the two seeded workflows and in any
custom one built before this change — gets:

  * responsible role Recruiter (was HR Manager), so the recruiter who owns
    the application stage also owns its verification and no second role is
    needed for an application to proceed;
  * the SCREEN_OUT decision, routed to the workflow's "Rejected" terminal.

Idempotent: a stage already so configured is left alone. Reversal puts the
role and decisions back but keeps the transition rows, which are harmless.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")
    StageTransition = apps.get_model("workflows", "StageTransition")
    Role = apps.get_model("accounts", "Role")

    recruiter = Role.objects.filter(code="recruiter").first()
    for stage in WorkflowStage.objects.filter(kind="hr_verification", is_active=True):
        changed = []
        if recruiter is not None and stage.responsible_role_id != recruiter.pk:
            stage.responsible_role = recruiter
            changed.append("responsible_role")
        decisions = list(stage.allowed_decisions or [])
        if "screen_out" not in decisions:
            decisions.append("screen_out")
            stage.allowed_decisions = decisions
            changed.append("allowed_decisions")
        if stage.name == "HR verification":
            stage.name = "Recruiter verification"
            changed.append("name")
        if changed:
            stage.save(update_fields=changed + ["updated_at"])

        rejected = (
            WorkflowStage.objects.filter(
                workflow_id=stage.workflow_id, kind="terminal", is_won=False, is_active=True
            )
            .order_by("order")
            .first()
        )
        if rejected is not None and not StageTransition.objects.filter(
            from_stage=stage, on_decision="screen_out", is_active=True
        ).exists():
            StageTransition.objects.create(
                from_stage=stage, on_decision="screen_out", to_stage=rejected
            )


def backwards(apps, schema_editor):
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")
    Role = apps.get_model("accounts", "Role")
    hr_manager = Role.objects.filter(code="hr_manager").first()
    for stage in WorkflowStage.objects.filter(kind="hr_verification", is_active=True):
        stage.allowed_decisions = [d for d in (stage.allowed_decisions or []) if d != "screen_out"]
        if hr_manager is not None:
            stage.responsible_role = hr_manager
        if stage.name == "Recruiter verification":
            stage.name = "HR verification"
        stage.save()


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0002_screen_out_decision"),
        # Pinned, not "__latest__": the floating form retroactively re-orders
        # history the moment ANY new accounts migration lands, and Django then
        # refuses to migrate at all.
        ("accounts", "0002_import_action"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
