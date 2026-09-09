"""
Register the product's periodic tasks with the database-backed beat scheduler.

WHY THIS IS A COMMAND AND NOT A MIGRATION
-----------------------------------------
It used to be five data migrations, one per module. That worked while there was
one organization and one schedule, but it makes the schedule a property of the
migration history rather than of the deployment: re-creating the database from
regenerated migrations silently loses every scheduled job, and nothing fails --
the system simply stops accruing leave and stops purging candidate PII, quietly,
until somebody notices months later.

That is exactly what happened when this project regenerated its migration
history: `makemigrations` reproduces schema, not hand-written RunPython, so the
five schedule rows vanished. Recovering them by hand is what this command is.

Run from `deploy/entrypoint.sh` alongside `manage.py check`, so the schedule is
reasserted on every deploy and drift cannot accumulate. Idempotent by
construction: `update_or_create` on the task name.

The schedules stay per-DEPLOYMENT for now. When the tasks are made
organization-aware they become dispatchers that fan out one subtask per
organization, which is deliberately NOT the same as one beat row per
organization -- creating a customer must never require writing to the
scheduler.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

#: (name, task, schedule). A dict with `every`/`period` is an interval; a dict
#: of cron fields is a crontab. Names are the identity used to update in place,
#: so changing one creates a second row rather than editing the first.
SCHEDULES: tuple[tuple[str, str, dict], ...] = (
    (
        "Leave: monthly accrual",
        "leave.monthly_accrual",
        {"minute": "0", "hour": "2", "day_of_month": "1"},
    ),
    (
        "Leave: short-leave conversion",
        "leave.convert_short_leave",
        {"minute": "30", "hour": "2", "day_of_month": "1"},
    ),
    (
        "Leave: absence flag",
        "leave.flag_absences",
        {"minute": "0", "hour": "7"},
    ),
    (
        "eSSL attendance punch sync (every 5 minutes)",
        "attendance.sync_essl",
        {"every": 5, "period": "minutes"},
    ),
    (
        # 03:15, off-peak and deliberately not on the hour: a purge that
        # collides with every other nightly job is a purge that gets blamed for
        # everything.
        "Candidate retention purge",
        "recruitment.purge_expired_candidates",
        {"minute": "15", "hour": "3"},
    ),
    (
        # 03:40, twenty-five minutes after the retention purge, so the two do
        # not contend.
        "Candidate import staging purge",
        "imports.purge_staging_pii",
        {"minute": "40", "hour": "3"},
    ),
    (
        "Google Forms response sync (every 10 minutes)",
        "recruitment.sync_google_form_responses",
        {"every": 10, "period": "minutes"},
    ),
)


class Command(BaseCommand):
    help = "Create or update the periodic task rows the product needs."

    def handle(self, *args, **options):
        from django_celery_beat.models import (
            CrontabSchedule,
            IntervalSchedule,
            PeriodicTask,
        )

        created = updated = 0
        for name, task, spec in SCHEDULES:
            if "every" in spec:
                schedule, _ = IntervalSchedule.objects.get_or_create(
                    every=spec["every"], period=spec["period"]
                )
                defaults = {"task": task, "interval": schedule, "crontab": None}
            else:
                schedule, _ = CrontabSchedule.objects.get_or_create(
                    minute=spec.get("minute", "*"),
                    hour=spec.get("hour", "*"),
                    day_of_week=spec.get("day_of_week", "*"),
                    day_of_month=spec.get("day_of_month", "*"),
                    month_of_year=spec.get("month_of_year", "*"),
                )
                defaults = {"task": task, "crontab": schedule, "interval": None}

            _, was_created = PeriodicTask.objects.update_or_create(
                name=name, defaults={**defaults, "enabled": True}
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"Beat schedule synced: {created} created, {updated} updated "
                f"({len(SCHEDULES)} tasks)."
            )
        )
