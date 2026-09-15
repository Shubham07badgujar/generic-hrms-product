"""
META-TEST: the access system checks are actually registered and actually bite.

`core/access/checks.py` calls itself "the single most important structural
difference from the previous system": it fails `manage.py check` -- and
therefore CI and `deploy/entrypoint.sh` -- for any API view that is neither
RBAC-mapped nor explicitly exempt.

It spent some time not running. The checks are registered by `@register(...)`
at module import time, and nothing imported the module: `core` had no
AppConfig, so `manage.py check` reported "no issues" while executing none of
them, and two genuinely unmapped views sat behind a green build.

A guarantee nobody verifies is a comment. These tests verify it:

  * the checks are in Django's registry at all (the failure that occurred);
  * the view check still returns an error for an unmapped view (so it cannot
    be quietly reduced to a no-op that passes vacuously);
  * the whole suite is clean right now.

This is the same "guard the guard" reasoning as
`tests/cutover/test_ceo_write_walker.py::test_the_walker_actually_found_the_api`
-- a checker that silently stops checking reports success forever.
"""

from __future__ import annotations

import pytest
from django.core.checks import ERROR, CheckMessage, Error
from django.core.checks.registry import registry

pytestmark = pytest.mark.meta

#: Every check `core/access/checks.py` is expected to contribute.
EXPECTED_CHECKS = {
    "check_all_api_views_are_mapped",
    "check_every_resource_has_a_spec",
    "check_role_invariants",
    # Reports which apps still read across organizations at the manager. It is
    # the check that would have said `TenantManager` was wired to nothing, so
    # it gets the same guard against silently not running as the others.
    "check_tenant_manager_rollout",
}


def _registered_access_checks() -> set[str]:
    return {
        fn.__name__
        for fn in registry.get_checks()
        if getattr(fn, "__module__", "") == "core.access.checks"
    }


def test_the_access_checks_are_registered():
    """
    The regression that motivated this file.

    If `core.apps.CoreConfig.ready()` stops importing `core.access.checks` --
    or `core` stops being an installed app -- every assertion below still
    passes while protecting nothing, because the checks simply never run.
    """
    missing = EXPECTED_CHECKS - _registered_access_checks()

    assert not missing, (
        f"Access system checks are not registered: {sorted(missing)}. "
        "`manage.py check` is passing vacuously and unmapped API views will "
        "reach production. See core/apps.py."
    )


def test_the_view_check_still_rejects_an_unmapped_view():
    """
    The check must fail a view declaring neither `access_resource` nor
    `access_exempt`. Registration alone is not enough: a check that returns
    `[]` unconditionally is registered and useless.
    """
    from rest_framework.views import APIView

    from core.access import checks

    class UnmappedProbeView(APIView):
        """Declares nothing. The check exists to catch exactly this."""

    errors = checks.check_one_view(UnmappedProbeView, "api/v1/probe/")

    assert any(
        isinstance(e, Error) and e.id == "access.E001" for e in errors
    ), f"An unmapped view produced no access.E001. Errors: {errors}"


@pytest.mark.django_db
def test_the_project_currently_passes_every_access_check(roles):
    """
    No unmapped view, no unregistered resource, no invalid role.

    Needs the database and the seeded catalogue: `check_role_invariants`
    queries `Role`, and running it against an empty table would assert nothing
    about the roles the product actually ships.
    """
    messages: list[CheckMessage] = []
    for fn in registry.get_checks():
        if getattr(fn, "__module__", "") == "core.access.checks":
            messages.extend(fn(None) or [])

    # ERRORS only. This test was written when every access check could return
    # nothing but errors, so it asserted the whole list was empty. The rollout
    # check reports a WARNING by design -- an unfinished per-app rollout is a
    # known state, not a broken build, and deploys run `--fail-level ERROR` for
    # the same reason. What that warning says is asserted exactly, in both
    # directions, in tests/tenancy/test_tenant_manager.py.
    errors: list[Error] = [m for m in messages if m.level >= ERROR]

    assert not errors, "Access checks report errors:\n  " + "\n  ".join(
        f"{e.id} {e.msg}" for e in errors
    )
