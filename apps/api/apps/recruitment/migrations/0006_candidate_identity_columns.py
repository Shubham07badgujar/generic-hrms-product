"""
Candidate identity, step 1 of 4: ADD ONLY.

Deliberately carries no constraint changes. Django's autodetector wanted to add
the new unique constraint and the lawful-basis check in the same migration that
creates the columns they read — which on any real table means the check runs
against rows that have not been backfilled yet and the deploy dies half way.

The sequence is expand → backfill (0007) → verify (0008) → contract (0009), so
that every constraint is added only once the data can satisfy it, and a failed
repair leaves no half-applied unique index behind.

Everything here is either catalog-only (DROP NOT NULL) or `ADD COLUMN NULL`,
both metadata-only in PostgreSQL 11+. The three new indexes are the only real
work, and they are on a table measured in thousands of rows.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0005_decisionoverride_new_stage_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CandidateExternalRef",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("source", models.CharField(db_index=True, max_length=40)),
                ("external_id", models.CharField(max_length=128)),
            ],
            options={"ordering": ["source", "external_id"]},
        ),
        migrations.AddField(
            model_name="candidateexternalref",
            name="candidate",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="external_refs",
                to="recruitment.candidate",
            ),
        ),
        migrations.AddField(
            model_name="candidateexternalref",
            name="created_by",
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="candidateexternalref",
            name="updated_by",
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="candidate",
            name="email_normalized",
            field=models.EmailField(blank=True, db_index=True, editable=False, max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="phone_e164",
            field=models.CharField(blank=True, db_index=True, editable=False, max_length=16, null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="legal_basis",
            field=models.CharField(
                blank=True,
                choices=[
                    ("consent", "Consent given directly"),
                    ("voluntarily_provided", "Voluntarily provided (s.7(a))"),
                    ("employer_subscription", "Employer platform subscription"),
                    ("legacy_unrecorded", "Legacy — basis not recorded"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="candidate",
            name="notice_due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # DROP NOT NULL. Catalog-only in Postgres: instant, no table rewrite.
        migrations.AlterField(
            model_name="candidate",
            name="email",
            field=models.EmailField(blank=True, db_index=True, max_length=254, null=True),
        ),
        migrations.AlterField(
            model_name="candidate",
            name="source",
            field=models.CharField(db_index=True, default="direct", max_length=40),
        ),
    ]
