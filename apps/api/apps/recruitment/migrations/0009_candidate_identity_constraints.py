"""
Candidate identity, step 4 of 4: CONTRACT.

Only reached once 0007 has populated the columns and 0008 has proved the data
can satisfy what follows.

Three guarantees land here:

  * `uniq_active_candidate_email_norm` — one active candidate per address,
    case-insensitively. Replaces a case-sensitive index that admitted the same
    person twice under different capitalisation.
  * `ck_candidate_has_lawful_basis` — no candidate may exist with neither
    recorded consent nor another lawful basis. Consent was previously enforced
    only in a serializer, so any service, shell or management command could
    write a basis-less record.
  * `ix_candidate_phone_active` — an INDEX, not a unique constraint. Two people
    legitimately share a handset, most often in exactly the segment this
    importer serves. Phone matches a candidate; it does not identify one.

Fully reversible: every operation is a constraint or index that can be dropped.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("recruitment", "0008_assert_candidate_email_unique")]

    operations = [
        migrations.RemoveConstraint(
            model_name="candidate",
            name="uniq_active_candidate_email",
        ),
        migrations.AddConstraint(
            model_name="candidate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("email_normalized",),
                name="uniq_active_candidate_email_norm",
            ),
        ),
        migrations.AddConstraint(
            model_name="candidate",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("consent_given", True),
                    models.Q(("legal_basis", ""), _negated=True),
                    _connector="OR",
                ),
                name="ck_candidate_has_lawful_basis",
            ),
        ),
        migrations.AddIndex(
            model_name="candidate",
            index=models.Index(
                condition=models.Q(("is_active", True)),
                fields=["phone_e164"],
                name="ix_candidate_phone_active",
            ),
        ),
        migrations.AddConstraint(
            model_name="candidateexternalref",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("source", "external_id"),
                name="uniq_active_external_ref",
            ),
        ),
    ]
