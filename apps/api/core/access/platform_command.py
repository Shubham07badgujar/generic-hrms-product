"""
A management command that administers the DEPLOYMENT, not one customer.

Provisioning, plans, the platform operator, purge, the demo estate: these run
on the server, typed by an operator, and they legitimately touch every
organization and the platform's own tables. From release 2 the command runs as
the confined runtime role like everything else, so it would otherwise see
nothing -- and a seeding command that silently writes into a void is worse
than one that fails.

So a platform command declares itself, once, by subclassing this. The bypass
is entered around `execute()`, which means the whole command is also one
transaction: an interrupted provision leaves nothing half-built.

A command that administers ONE organization is NOT this. Those take an
`--organization` and bind it with `acting_as`, and they stay confined.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction


class PlatformCommand(BaseCommand):
    """Base for deployment-level commands. See the module docstring."""

    #: Named in the bypass log line, so "who crossed organizations, and why"
    #: has an answer for commands as well as for requests.
    platform_reason = ""

    def execute(self, *args, **options):
        from .platform_bypass import platform_bypass

        reason = self.platform_reason or f"management command: {type(self).__module__}"
        with transaction.atomic(), platform_bypass(reason=reason, system=True):
            return super().execute(*args, **options)
