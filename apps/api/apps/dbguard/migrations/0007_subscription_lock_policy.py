"""
Let a customer LOCK its own subscription row, while still never writing it.

THE BUG THIS FIXES. `reserve_seats` is the seat check, and it reads the
subscription with `SELECT ... FOR UPDATE` -- the lock is the whole reason it
is a service rather than a `count()`, because two concurrent hires must not
both decide there is room. PostgreSQL applies the UPDATE policy's USING
clause to any LOCKING read, not just to writes, and release 1 gave this table
a SELECT policy only. So under the confined runtime role the locking read
matched NO rows, `reserve_seats` concluded "no subscription", and "no
subscription" means UNLIMITED -- correctly, for a self-hosted deployment that
never bought seats. The result was a seat limit that silently stopped being
enforced, with nothing in the logs to say so.

    plain SELECT        -> 1 row
    SELECT FOR UPDATE   -> 0 rows      (measured, before this migration)

THE FIX. An UPDATE policy whose USING clause admits the organization's own
row -- which is what permits the lock -- and whose WITH CHECK is FALSE, so an
actual UPDATE by the runtime role is refused, loudly, rather than silently
changing nothing. Billing writes go through `platform_bypass`, whose role
holds BYPASSRLS and is unaffected.

So a customer may read its plan and lock it for the duration of its own hire,
and may still never change what it is paying for.
"""

from django.db import migrations

TABLE = "platform_subscription"
POLICY = "subscription_lock"

FORWARD = f"""
CREATE POLICY {POLICY} ON {TABLE}
    FOR UPDATE
    USING (organization_id = dbguard_current_org())
    WITH CHECK (false);
"""

REVERSE = f"DROP POLICY IF EXISTS {POLICY} ON {TABLE};"


class Migration(migrations.Migration):
    dependencies = [("dbguard", "0006_runtime_grants")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
