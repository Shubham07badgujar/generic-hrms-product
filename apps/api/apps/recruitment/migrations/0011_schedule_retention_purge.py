"""
Register the retention purge with Celery beat.

Beat uses `django_celery_beat.schedulers:DatabaseScheduler`, so a schedule is a
database row rather than a settings dict. That is why this is a migration: a
`CELERY_BEAT_SCHEDULE` entry in settings would be read by nobody.

Without this row the purge exists as code and never runs, which is exactly the
state the retention policy has been in since it was written — documented in
settings, pointed at a service file that did not exist, executed never.

Reversible: removes the schedule and leaves the task callable by hand.
"""

from django.db import migrations

TASK_NAME = "recruitment.purge_expired_candidates"
SCHEDULE_NAME = "Candidate retention purge (nightly)"


def add_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # 03:15 daily. Off-peak, and deliberately not on the hour — a purge that
    # collides with every other nightly job is a purge that gets blamed for
    # unrelated load.
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="15", hour="3", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task": TASK_NAME,
            "crontab": schedule,
            "enabled": True,
            "description": (
                "Anonymises candidate personal data past its retention period. "
                "Hiring records are kept; identifiers are destroyed in place."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=SCHEDULE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0010_consent_ledger"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(add_schedule, remove_schedule)]
