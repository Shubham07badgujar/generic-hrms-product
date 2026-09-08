"""
Register the leave module's periodic tasks with the database-backed beat
scheduler — the same pattern as recruitment's retention purge.

  * Monthly accrual: 1st of every month, 02:00 — credits the month's PL/CL
    accrual for every employee on an accruing policy.
  * Short-leave conversion: 1st of every month, 02:30 — settles the PREVIOUS
    month's short-leave hours into day deductions before payroll runs.
  * Absence flag: daily, 07:00 — flags uninformed multi-day absence to HR
    (a no-op until the attendance module exists).
"""

from django.db import migrations

TASKS = [
    ("Leave: monthly accrual", "leave.monthly_accrual",
     {"minute": "0", "hour": "2", "day_of_month": "1"}),
    ("Leave: short-leave conversion", "leave.convert_short_leave",
     {"minute": "30", "hour": "2", "day_of_month": "1"}),
    ("Leave: absence flag", "leave.flag_absences",
     {"minute": "0", "hour": "7"}),
]


def add_schedules(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, task, cron in TASKS:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=cron.get("minute", "*"), hour=cron.get("hour", "*"),
            day_of_week=cron.get("day_of_week", "*"),
            day_of_month=cron.get("day_of_month", "*"),
            month_of_year=cron.get("month_of_year", "*"),
        )
        PeriodicTask.objects.update_or_create(
            name=name,
            defaults={"task": task, "crontab": schedule, "enabled": True},
        )


def remove_schedules(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[n for n, _, _ in TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0002_leavepolicy_accrual_per_month_and_more"),
        ("django_celery_beat", "0001_initial"),
    ]
    operations = [migrations.RunPython(add_schedules, remove_schedules)]
