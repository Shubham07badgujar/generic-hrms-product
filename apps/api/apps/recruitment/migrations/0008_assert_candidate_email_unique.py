"""
Candidate identity, step 3 of 4: VERIFY. Writes nothing.

The old index was case-SENSITIVE, so `Ravi@x.com` and `ravi@x.com` were two
different people as far as the database was concerned. 0009 replaces it with a
case-insensitive one, and if any two active candidates now normalize to the same
address that index cannot be built.

This migration exists SO THAT NOTHING GUESSES. A data migration must never pick
which of two real people's records survives — one of them may have a live
application, an interview history and an offer. It fails, names the rows, and
points at the repair command.

It is a separate migration from 0009 for a specific reason: a failure here
leaves the database working and un-deduped, with no partially applied unique
index to unpick. 0009 either runs cleanly or does not run.
"""

from django.db import migrations
from django.db.models import Count


def assert_no_email_collisions(apps, schema_editor):
    Candidate = apps.get_model("recruitment", "Candidate")

    collisions = (
        Candidate.objects.filter(is_active=True, email_normalized__isnull=False)
        .values("email_normalized")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by("-n")
    )

    offending = list(collisions[:20])
    if not offending:
        return

    total = collisions.count()
    lines = []
    for row in offending:
        ids = list(
            Candidate.objects.filter(
                is_active=True, email_normalized=row["email_normalized"]
            ).values_list("id", flat=True)
        )
        # The address is the thing that collided, so it has to appear for the
        # repair to be actionable. Everything else about the people involved
        # stays out of the migration log.
        lines.append(f"  {row['email_normalized']}: {row['n']} rows {ids}")

    raise RuntimeError(
        "Cannot make candidate email case-insensitive: "
        f"{total} address(es) are held by more than one active candidate.\n"
        + "\n".join(lines)
        + (
            "\n\nNothing has been changed. Resolve these first:\n"
            "  manage.py audit_candidate_identity          # full report\n"
            "  manage.py audit_candidate_identity --apply  # retire empty duplicates\n"
            "\nRecords carrying applications, offers or decisions are never "
            "touched automatically and need a human."
        )
    )


class Migration(migrations.Migration):

    dependencies = [("recruitment", "0007_backfill_candidate_identity")]

    operations = [
        migrations.RunPython(assert_no_email_collisions, migrations.RunPython.noop)
    ]
