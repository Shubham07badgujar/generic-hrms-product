"""
The one named door through row-level security.

    with transaction.atomic(), platform_bypass(reason="console headcount", principal=request.user):
        ...queries that must span organizations...

Some work is platform work by nature and has to see every organization: the
console's organization list and headcount, provisioning, purge, the support-
grant bookkeeping, deployment-wide sweeps. Under row-level security those
queries would see nothing, so they enter this block, which switches the
database session to `generic_hrms_platform` -- a NOLOGIN role with BYPASSRLS
-- for the rest of the current transaction.

It is deliberately awkward to reach:

  * a REASON is required and logged, so every crossing is findable;
  * the principal must be a platform administrator, or the caller must declare
    itself system work (`system=True`: a management command or a platform
    task). A tenant request -- whatever its roles -- is refused;
  * it must run inside a transaction: `SET LOCAL ROLE` ends when the
    transaction does, so the bypass cannot leak into the next request on a
    reused connection even if the block is exited abnormally.

WHAT IT IS NOT. It defends against ACCIDENT -- a query that forgets its
organization filter still sees one organization. It is not a defence against
malicious code already executing inside the application process, which could
issue the same SQL; the application layer and code review are the control for
that. Said plainly so it is not over-claimed.

RELEASE 1: the application still connects as the table owner, which RLS does
not constrain, so today this changes privileges (to the platform role's DML
grants) without changing what is visible. Routing the `all_orgs()` call sites
through it is release-2 work.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from django.db import DatabaseError, connection

logger = logging.getLogger("hrms.dbguard")

PLATFORM_ROLE = "generic_hrms_platform"


class PlatformBypassRefused(PermissionError):
    """The caller is not entitled to cross organizations."""


@contextmanager
def as_runtime_role():
    """
    Step back DOWN to the confined runtime role, inside a bypass.

    The inverse of `platform_bypass`, and it exists for one case: work that
    runs on a platform endpoint but must NOT see across organizations.
    Support Access is the example -- the grant row is found platform-wide,
    but the customer's configuration is then read bound to that one
    organization, and the database should be the thing enforcing it rather
    than the tenant manager alone.

    Restores the role in force before, so nesting is safe.
    """
    from django.conf import settings

    runtime = settings.DATABASES["default"].get("USER")
    if not runtime:
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        previous = cursor.fetchone()[0]
        if previous == runtime:
            yield
            return
        cursor.execute(f"SET LOCAL ROLE {runtime}")
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('role', %s, true)", [previous])
        except DatabaseError:
            pass


@contextmanager
def platform_bypass(*, reason: str, principal=None, system: bool = False):
    reason = (reason or "").strip()
    if not reason:
        raise PlatformBypassRefused("platform_bypass needs a reason; every crossing is logged.")
    if not system and not getattr(principal, "is_platform_admin", False):
        raise PlatformBypassRefused(
            "Only a platform administrator, or declared system work, may cross "
            "organizations. A tenant request never may."
        )
    if not connection.in_atomic_block:
        raise PlatformBypassRefused(
            "platform_bypass must run inside transaction.atomic(): SET LOCAL ROLE "
            "ends with the transaction, which is what keeps it from leaking."
        )

    logger.warning(
        "dbguard.platform_bypass reason=%r principal=%s system=%s",
        reason, getattr(principal, "pk", None), system,
    )
    with connection.cursor() as cursor:
        # Restore the role that was active BEFORE, not the session role:
        # `RESET ROLE` would silently turn a caller already running as the
        # runtime role back into the login role on exit.
        cursor.execute("SELECT current_user")
        previous = cursor.fetchone()[0]
        cursor.execute(f"SET LOCAL ROLE {PLATFORM_ROLE}")
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('role', %s, true)", [previous]
                )
        except DatabaseError:
            # An aborted transaction refuses the reset; the rollback that must
            # follow ends the SET LOCAL anyway.
            pass
