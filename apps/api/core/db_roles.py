"""
Which database role runs what.

From release 2 the application connects as `generic_hrms_app`: it owns no
table, so row-level security applies to it, and it cannot delete the audit
trail. That is the whole point -- but two jobs genuinely need ownership:

  * `migrate`, because schema changes must be made by the table owner;
  * creating the TEST database, for the same reason.

Both reach the owner through `as_owner()` below, which swaps the credentials
on the default connection for the duration of a block and puts them back. It
is deliberately not a second Django database alias: an alias can be reached
by an ordinary queryset with `.using("owner")`, and a second door into the
owner's privileges is exactly what this release exists to close.

`platform_bypass` is the OTHER escape, and the two are not interchangeable:
that one widens visibility inside a request (`SET LOCAL ROLE` to the
BYPASSRLS role, in a transaction, for a platform principal); this one changes
who connects, outside request handling entirely.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.conf import settings
from django.db import connections

#: Keys that identify the ROLE, and nothing else. Host, port and database are
#: never taken from the owner URL at runtime -- settings has already checked
#: they match, and copying them here would make a mismatch survivable.
_ROLE_KEYS = ("USER", "PASSWORD")


def owner_is_configured() -> bool:
    return bool(getattr(settings, "DATABASE_OWNER", None))


def owner_role() -> str | None:
    owner = getattr(settings, "DATABASE_OWNER", None)
    return owner.get("USER") if owner else None


@contextmanager
def as_owner(alias: str = "default"):
    """
    Run a block on the owner's credentials.

    Connections are closed on the way in and on the way out: a connection
    already open belongs to the other role, and one opened inside this block
    must not be reused by ordinary code afterwards.

    With no owner URL configured this is a no-op, so a deployment that has not
    split its roles behaves exactly as it did before.
    """
    owner = getattr(settings, "DATABASE_OWNER", None)
    if not owner:
        yield False
        return

    config = settings.DATABASES[alias]
    previous = {key: config.get(key) for key in _ROLE_KEYS}
    connections[alias].close()
    config.update({key: owner.get(key) for key in _ROLE_KEYS})
    try:
        yield True
    finally:
        connections[alias].close()
        config.update(previous)


def current_database_role(alias: str = "default") -> str:
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT current_user")
        return cursor.fetchone()[0]


def schema_ownership(alias: str = "default") -> tuple[bool, str, str | None]:
    """
    (may this role change the schema, who it is, who owns the tables).

    Ownership is read from the tables themselves rather than assumed from the
    role name, so a renamed role or a database restored elsewhere still gets a
    truthful answer. An empty database has no owner yet, so the question
    becomes whether this role may create in it at all.
    """
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT current_user, current_setting('is_superuser') = 'on'")
        role, is_superuser = cursor.fetchone()
        cursor.execute(
            "SELECT tableowner, count(*) FROM pg_tables WHERE schemaname = 'public' "
            "GROUP BY tableowner ORDER BY count(*) DESC LIMIT 1"
        )
        row = cursor.fetchone()
        table_owner = row[0] if row else None
        if table_owner is None:
            cursor.execute(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
            )
            return bool(cursor.fetchone()[0]), role, None
        if is_superuser:
            return True, role, table_owner
        cursor.execute("SELECT pg_has_role(current_user, %s, 'USAGE')", [table_owner])
        return bool(cursor.fetchone()[0]), role, table_owner
