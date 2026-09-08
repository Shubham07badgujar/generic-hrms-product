"""
Give every existing job opening an application token, then make the column
NOT NULL.

Its own migration, after the additive one, so the ADD COLUMN NULL commits
before any row is touched and a failure here leaves a nullable column rather
than a half-applied constraint. Tokens are drawn the same way the model draws
them; the helper is copied in rather than imported because a migration must
reproduce what it did the day it ran, whatever the model does later.
"""

import secrets

from django.db import migrations, models


def _token() -> str:
    return secrets.token_urlsafe(24)


def backfill(apps, schema_editor):
    JobOpening = apps.get_model("recruitment", "JobOpening")
    for job in JobOpening.objects.filter(application_token__isnull=True).only("pk"):
        # One UPDATE per row: a bulk_update would need the token generated
        # client-side anyway, and there are dozens of jobs, not millions.
        JobOpening.objects.filter(pk=job.pk).update(application_token=_token())


class Migration(migrations.Migration):
    dependencies = [
        ("recruitment", "0014_application_links_and_candidate_notifications"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jobopening",
            name="application_token",
            field=models.CharField(
                db_index=True, editable=False, max_length=64, unique=True
            ),
        ),
    ]
