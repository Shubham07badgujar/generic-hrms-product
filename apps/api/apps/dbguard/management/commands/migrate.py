"""
`migrate`, refused unless the connection owns the tables.

From release 2 the runtime connects as `generic_hrms_app`, which owns nothing.
Running migrations through it would fail partway -- or worse, succeed for the
statements it happens to be allowed and leave the schema half-changed. So the
check happens before anything runs, and the message says exactly what to do.

Deliberately a refusal rather than a silent switch to the owner: a deployment
should be able to withhold the owner password from the application process
entirely, and a command that quietly escalated would hide that it had not.
"""

from __future__ import annotations

from django.core.management.base import CommandError
from django.core.management.commands.migrate import Command as MigrateCommand

from core.db_roles import owner_role, schema_ownership


class Command(MigrateCommand):
    def handle(self, *args, **options):
        database = options.get("database") or "default"
        if self._is_postgresql(database):
            allowed, role, table_owner = schema_ownership(database)
            if not allowed:
                owner = owner_role() or "the owner role"
                raise CommandError(
                    f"Refusing to migrate as {role!r}: the tables are owned by "
                    f"{table_owner!r} and this role cannot change the schema.\n"
                    f"Run migrations with the owner connection, e.g.\n"
                    f"    DATABASE_URL=$DATABASE_OWNER_URL python manage.py migrate\n"
                    f"(the owner role is {owner!r}; the application role is never "
                    f"granted ownership, which is what row-level security relies on)."
                )
        return super().handle(*args, **options)

    @staticmethod
    def _is_postgresql(database: str) -> bool:
        from django.db import connections

        return connections[database].vendor == "postgresql"
