"""
Keep the database's idea of "which organization" identical to the application's.

Row-level security (apps/dbguard, migration 0004) reads two session settings:

    app.org_id    the organization the current code is acting for
    app.user_id   the principal, for the membership table's policy

This module is the ONLY writer of those settings, and it does not keep a copy
of the answer. It reads the same ContextVars every binding point in the
application already writes -- `RequestContextMiddleware`, the JWT
authenticator's `_bind_organization`, `acting_as`, `set_current_org_id` -- at
the moment a query is about to run. So there are not four call sites that must
each remember to update the database; there is one place that asks, just in
time, and cannot be forgotten by the next binding point somebody adds.

HOW. An execute wrapper is installed on every database connection as it opens.
Before each statement it compares the bound (org, user) with what it last sent
on that connection and sends `set_config(...)` only when needed -- so a request
that stays in one organization pays one extra round trip, not one per query.

WHEN A TRANSACTION ENDS. `set_config` is transactional: a ROLLBACK (or ROLLBACK
TO SAVEPOINT) reverts it. So "what was sent" is remembered together with the
exact stack of transaction/savepoint blocks that were open at the time, each
block tagged with a unique serial. If any block in that recorded stack has since
ended -- committed or rolled back, it cannot tell which -- the value is sent
again. A redundant re-send is possible; missing a reverted value is not. That
matters beyond tests: without it, a rolled-back transaction would leave the
database believing an older organization than the application, which is the
stale-organization shape this whole design exists to prevent.

FAIL CLOSED. Nothing bound => both settings are the empty string => the
policies' `nullif(...)::uuid` is NULL => no tenant row matches.

RELEASE 1. The application still connects as the table owner, which RLS does
not apply to, so this changes no behaviour yet; the RLS tests exercise it by
switching to the non-owner runtime role.
"""

from __future__ import annotations

import itertools

from django.db.backends.signals import connection_created

_CACHE_ATTR = "_dbguard_sent"
_SERIAL_ATTR = "_dbguard_serial"
_serials = itertools.count(1)


def _bound() -> tuple[str, str]:
    from core.middleware import get_current_org_id, get_current_user

    org = get_current_org_id()
    user = get_current_user()
    user_pk = getattr(user, "pk", None) if user is not None else None
    return (str(org) if org else "", str(user_pk) if user_pk else "")


def _block_stack(connection) -> tuple[int, ...]:
    """Unique serials of the transaction/savepoint blocks open right now."""
    serials = []
    for block in getattr(connection, "atomic_blocks", []):
        serial = getattr(block, _SERIAL_ATTR, None)
        if serial is None:
            serial = next(_serials)
            setattr(block, _SERIAL_ATTR, serial)
        serials.append(serial)
    return tuple(serials)


def _still_valid(sent, wanted, stack) -> bool:
    if sent is None:
        return False
    sent_wanted, sent_stack = sent
    # Every block that was open when we sent must still be open, in order.
    return sent_wanted == wanted and stack[: len(sent_stack)] == sent_stack


#: Transaction control needs no organization, and must never be preceded by a
#: query: a failed statement leaves the transaction aborted, and the very next
#: thing Django does is roll back through this same wrapper. Sending
#: `set_config` there would raise InFailedSqlTransaction and bury the real
#: error -- an IntegrityError the caller was about to handle -- under a
#: meaningless one.
_CONTROL_STATEMENTS = ("SAVEPOINT", "RELEASE", "ROLLBACK", "COMMIT", "BEGIN", "SET ")


def _is_transaction_control(sql) -> bool:
    return str(sql).lstrip()[:24].upper().startswith(_CONTROL_STATEMENTS)


def _wrapper(execute, sql, params, many, context):
    connection = context["connection"]
    if _is_transaction_control(sql) or connection.needs_rollback:
        return execute(sql, params, many, context)
    wanted = _bound()
    stack = _block_stack(connection)
    if not _still_valid(getattr(connection, _CACHE_ATTR, None), wanted, stack):
        # Our OWN plain cursor, for two reasons. Going through Django's cursor
        # would re-enter this wrapper -- and `context["cursor"]` is sometimes a
        # SERVER-SIDE cursor, which is worse than it sounds: `.iterator()` asks
        # for one, and executing a SELECT on a named cursor issues
        # `DECLARE ... CURSOR FOR SELECT set_config(...)`, which declares the
        # query and never runs it. The settings would silently not be applied,
        # this cache would record that they had been, and every later statement
        # on the connection would skip them too -- leaving the session unbound
        # for the rest of the request.
        with connection.connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.org_id', %s, false), "
                "set_config('app.user_id', %s, false)",
                wanted,
            )
        setattr(connection, _CACHE_ATTR, (wanted, stack))
    return execute(sql, params, many, context)


def _install(sender, connection, **kwargs):
    if connection.vendor != "postgresql":
        return
    if _wrapper not in connection.execute_wrappers:
        connection.execute_wrappers.append(_wrapper)
    # A brand-new session has neither setting; make the first query send them.
    setattr(connection, _CACHE_ATTR, None)


def install() -> None:
    """Called once from AppConfig.ready()."""
    connection_created.connect(_install, dispatch_uid="dbguard_db_context")
