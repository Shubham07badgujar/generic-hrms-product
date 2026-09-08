"""
Applications start at Recruiter verification — the holding stage retires.

The old pipelines opened with an "Application received" stage whose only
decision was the recruiter's Pass into verification. That step is gone: a
submitted application lands directly in Recruiter verification, so every
active APPLICATION-kind stage is deactivated here, its transitions with it,
and any application still parked on one moves to where its Pass would have
taken it (the stage its `pass` transition targets, or the workflow's
verification stage). Each move is written into the application's history the
same way `repair_application_stages` records one.

Idempotent: a second run finds no active APPLICATION stages and does nothing.
Reversal reactivates nothing — the retired stage is historical data.
"""

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    WorkflowStage = apps.get_model("workflows", "WorkflowStage")
    StageTransition = apps.get_model("workflows", "StageTransition")
    Application = apps.get_model("recruitment", "Application")
    ApplicationEvent = apps.get_model("recruitment", "ApplicationEvent")

    for stage in WorkflowStage.objects.filter(kind="application", is_active=True):
        onward = StageTransition.objects.filter(
            from_stage=stage, on_decision="pass", is_active=True
        ).first()
        target = onward.to_stage if onward else (
            WorkflowStage.objects.filter(
                workflow_id=stage.workflow_id, kind="hr_verification", is_active=True
            ).order_by("order").first()
        )
        if target is None:
            # A workflow with no verification stage is not one the seeds ever
            # built; leave it untouched rather than guess.
            continue

        for application in Application.objects.filter(current_stage=stage):
            application.current_stage = target
            application.save(update_fields=["current_stage", "updated_at"])
            ApplicationEvent.objects.create(
                application=application,
                kind="stage_changed",
                actor=None,
                actor_label="system · workflow update",
                from_stage=stage,
                to_stage=target,
                note=(
                    "Moved to Recruiter verification: applications now start "
                    "there directly, without the old 'Application received' step."
                ),
                detail={"workflow_update": "applications_start_at_verification"},
            )

        StageTransition.objects.filter(from_stage=stage).update(
            is_active=False, updated_at=timezone.now()
        )
        StageTransition.objects.filter(to_stage=stage).update(
            is_active=False, updated_at=timezone.now()
        )
        stage.is_active = False
        stage.save(update_fields=["is_active", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0003_recruiter_verification_and_screen_out"),
        ("recruitment", "0022_interview_calendar_error_interview_calendar_event_id_and_more"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
