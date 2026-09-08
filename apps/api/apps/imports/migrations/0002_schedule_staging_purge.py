"""
Register the staging-PII purge with Celery beat.

A migration rather than a settings entry, because beat runs the
`DatabaseScheduler` — a `CELERY_BEAT_SCHEDULE` dict would be read by nobody and
the policy would exist only as prose, which is exactly the state the candidate
retention policy was already found in.

Reversible: removes the schedule and leaves the task callable by hand.
"""

from django.db import migrations

TASK_NAME = "imports.purge_staging_pii"
SCHEDULE_NAME = "Candidate import staging PII purge (nightly)"


def add_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # 03:40, twenty-five minutes after the candidate retention purge, so the two
    # do not contend and a slow night is attributable to one of them.
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="40", hour="3", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task": TASK_NAME,
            "crontab": schedule,
            "enabled": True,
            "description": (
                "Strips names, contact details and the raw row copy from import "
                "staging rows: 24 hours for previews nobody committed, 7 days "
                "for committed batches. Counts, hashes, outcomes and audit "
                "records are kept; candidates and applications are untouched."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=SCHEDULE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(add_schedule, remove_schedule)]
