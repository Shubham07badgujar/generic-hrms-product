"""
Register the Google Forms response sync with Celery beat.

Same reasoning as 0011: beat reads the database, so a schedule is a row. The
task returns immediately when Google Forms is not configured, so this costs
nothing on a deployment that only uses the hosted form.
"""

from django.db import migrations

TASK_NAME = "recruitment.sync_google_form_responses"
SCHEDULE_NAME = "Google Forms response sync (every 10 minutes)"


def add_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    every, _ = IntervalSchedule.objects.get_or_create(every=10, period="minutes")
    PeriodicTask.objects.update_or_create(
        name=SCHEDULE_NAME,
        defaults={
            "task": TASK_NAME,
            "interval": every,
            "enabled": True,
            "description": (
                "Pulls new Google Form responses for published jobs into the "
                "recruitment pipeline. No-op unless GOOGLE_FORMS_CREDENTIALS_FILE is set."
            ),
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=SCHEDULE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("recruitment", "0017_external_form_item_map"),
        ("django_celery_beat", "0001_initial"),
    ]
    operations = [migrations.RunPython(add_schedule, remove_schedule)]
