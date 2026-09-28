"""
Every detail route in the product, probed with another organization's real id.

The matrix covers nine resources I chose by hand. This covers whatever the URL
resolver exposes, so a route added next year is included the day it is added --
the same reasoning as `tests/cutover/test_ceo_write_walker.py`, which walks
every route as the read-only CEO.

WHY REAL IDS
------------
The CEO walker fills its paths with random UUIDs, which is right for its
question: a read-only principal must be refused BEFORE the handler looks
anything up, so a 404 for a missing row is still a refusal.

That would be worthless here. `/api/v1/employees/<random uuid>/` returns 404
because nothing matches, in a single-tenant system and a multi-tenant one
alike. To mean anything the id must name a row that genuinely EXISTS and
genuinely belongs to somebody else -- so each route is filled with an id of the
right MODEL from organization B, and walked as organization A's admin.

Admin, deliberately: they hold Scope.ALL almost everywhere, which is exactly
the reach the tenant predicate has to override.

WHY IT CALIBRATES ITSELF
------------------------
A route only says something about tenancy if it SERVES the caller's own row.
One that answers 405 because the action is POST-only, or 404 because the
fixture uploaded no file, refuses both organizations equally -- and counting
those as "refused" would be scoring the test against itself. So each route is
asked for A's own row first, and only the ones that answer 200 are asserted
against.
"""

from __future__ import annotations

import pytest

from core.access.routewalk import concretize, detail_routes, model_of, rows_for

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

#: Excluded, each for a stated reason: session lifecycle carries no object, and
#: bootstrap is token-gated and throttled to 5/hour, so probing it would
#: exhaust the budget the dedicated bootstrap suite needs.
EXCLUDED = (
    "/api/v1/auth/login/",
    "/api/v1/auth/login/admin/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/change-password/",
    "/api/v1/bootstrap/admin/",
)

#: A refusal. 404 is the intended answer for another organization's row --
#: deliberately indistinguishable from one that does not exist.
REFUSED = {403, 404, 405}


def _probe_pairs(org_a, org_b):
    """
    (A's path, B's path, description) for each detail route.

    Both are filled from the SAME template, so the two requests differ in
    exactly one thing: whose row the id names.
    """
    from .conftest import across_organizations

    # Picking one row from EACH organization to aim the walker at: the
    # harness's own reach, not the product's. The requests it then makes are
    # ordinary confined ones.
    pairs = []
    with across_organizations():
        return _pairs_for(detail_routes(), org_a, org_b, pairs)


def _pairs_for(routes, org_a, org_b, pairs):
    for template, view in routes:
        if "pk" not in template:
            continue
        model = model_of(view)
        if model is None or not hasattr(model, "organization_id"):
            continue
        mine = rows_for(model, org_a.organization.pk).first()
        theirs = rows_for(model, org_b.organization.pk).first()
        if mine is None or theirs is None:
            continue
        my_path = concretize(template, mine.pk)
        their_path = concretize(template, theirs.pk)
        if not my_path or not their_path or my_path in EXCLUDED:
            continue
        pairs.append((my_path, their_path, f"{view.__name__} -> {model._meta.label}"))
    return pairs


def _discriminating(client, pairs):
    """Routes that serve the caller's own row, and can therefore answer."""
    return [pair for pair in pairs if client.get(pair[0]).status_code == 200]


#: The floor, set just under the 63 routes across 51 viewsets the fixture
#: actually reaches today.
#:
#: A floor of 8 was the honest number when `_build` created ten kinds of
#: object, and it stayed 8 while coverage grew -- which made it useless: the
#: fixture could have lost payroll, recruitment and offboarding entirely and
#: this test would still have passed, reporting a clean API surface it had not
#: walked. The margin is one or two routes, enough for a route to be
#: legitimately retired without a red build and nothing like enough for an app
#: to fall out unnoticed.
MIN_ROUTES = 60
MIN_VIEWSETS = 48

#: And the counts alone are not enough. 63 routes could all be departments and
#: designations while every module holding real HR data goes unwalked, so the
#: apps the brief names are asserted BY NAME.
MUST_REACH = {
    "attendance",
    "audit",
    "employees",
    "imports",
    "itaccounts",
    "leave",
    "offboarding",
    "onboarding",
    "organization",
    "payroll",
    "recruitment",
    "workflows",
}


def _apps_reached(discriminating):
    return {what.split(" -> ")[1].split(".")[0] for _m, _t, what in discriminating}


def test_the_walker_finds_routes_that_can_answer(org_a, org_b, api_for, capsys):
    """
    Guards the guard, and publishes what it guards.

    A collector that silently found nothing would make the assertion below pass
    forever while probing no routes at all. Not hypothetical: an earlier
    `concretize` substituted in the wrong order, produced zero walkable routes,
    and would have reported the entire API surface clean.

    The counts are printed rather than only asserted, because "the walker
    passed" and "the walker walked payroll" are different claims and only one
    of them belongs in a report.
    """
    client = api_for(org_a.admin)
    pairs = _probe_pairs(org_a, org_b)
    discriminating = _discriminating(client, pairs)
    viewsets = {what.split(" -> ")[0] for _m, _t, what in discriminating}
    reached = _apps_reached(discriminating)

    with capsys.disabled():
        print(
            f"\n  cross-tenant walk: {len(discriminating)} discriminating routes"
            f" / {len(viewsets)} viewsets / {len(reached)} apps"
            f"  (of {len(pairs)} probeable, {len(detail_routes())} total)"
        )

    assert len(discriminating) >= MIN_ROUTES, (
        f"Only {len(discriminating)} of {len(pairs)} detail routes served A's "
        f"own row, down from {MIN_ROUTES}. Either the fixture stopped creating "
        f"rows or routes were removed -- and until it is one of those, this "
        f"suite is proving less than it claims."
    )
    assert len(viewsets) >= MIN_VIEWSETS, (
        f"Coverage narrowed to {len(viewsets)} viewsets, below {MIN_VIEWSETS}."
    )
    missing = MUST_REACH - reached
    assert not missing, (
        f"These apps hold organization data and no route of theirs was walked: "
        f"{sorted(missing)}. Add a row for the missing model to "
        f"tests/tenancy/_extra.py -- a count that still clears the floor while "
        f"an entire module goes unasked is exactly the silence this exists to "
        f"prevent."
    )


def test_no_detail_route_serves_another_organizations_row(org_a, org_b, api_for):
    """
    The brief's question, over the whole surface rather than a chosen nine.

    Asserted only against routes that serve A's OWN row, so every refusal is
    attributable to tenancy and nothing else.
    """
    client = api_for(org_a.admin)
    leaked = []

    for _mine, theirs, what in _discriminating(client, _probe_pairs(org_a, org_b)):
        response = client.get(theirs)
        if response.status_code not in REFUSED:
            leaked.append(f"{response.status_code} {theirs}  ({what})")

    joined = "\n  ".join(leaked)
    assert not leaked, (
        "These routes serve A's own row AND organization B's row to the same "
        f"caller:\n  {joined}"
    )
