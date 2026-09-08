"""
Register the eSSL punch sync with django-celery-beat.

A data migration rather than a settings dict because the beat scheduler is
DatabaseScheduler: a CELERY_BEAT_SCHEDULE entry would be read by nobody.
The row ticks every 5 minutes; the task itself no-ops unless
ESSL_INTEGRATION_ENABLED is true and honours ESSL_SYNC_INTERVAL as a
minimum gap, so pace stays environment-governed.
"""

from django.db import migrations

TASK_NAME = "attendance.sync_essl"
SCHEDULE_NAME = "eSSL attendance punch sync (every 5 minutes)"


def add_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    every, _ = IntervalSchedule.objects.get_or_create(every=5, period="minutes")
    PeriodicTask.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task": TASK_NAME,
            "interval": every,
            "enabled": True,
            "description": (
                "Pulls new punches from every enabled eSSL device into the "
                "attendance module. No-op unless ESSL_INTEGRATION_ENABLED is set."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=SCHEDULE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0001_initial"),
        ("django_celery_beat", "0001_initial"),
    ]
    operations = [migrations.RunPython(add_schedule, remove_schedule)]
