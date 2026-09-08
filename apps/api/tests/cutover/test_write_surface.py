"""
Cutover gate 3: the write surface.

Found by the CEO walker's control test, which POSTed an empty body to every
route as an ordinary employee and got a 500 out of one of them.

The cause is a combination that looks harmless in isolation. A serializer that
declares `read_only_fields = fields` validates an empty body happily — there is
nothing to validate — and if its viewset still routes `create`, DRF then calls
`Model.objects.create()` with no fields at all. The database raises, Django
returns 500, and the response body carries a constraint name.

Two problems in one: an unvalidated write path, and an error page that tells an
unauthenticated-adjacent caller about the schema.

These viewsets never wanted `create`. Their rows are produced by services when
a workflow reaches the right point, and `post` is in `http_method_names` only
so the custom `@action` routes work. So the fix is to close `create` and keep
the actions — and this test makes the combination impossible to reintroduce.
"""

from __future__ import annotations

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status

pytestmark = pytest.mark.django_db


def _viewsets():
    """Every DRF viewset the resolver routes to, with its serializer class."""
    seen = {}

    def walk(resolver):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry)
            elif isinstance(entry, URLPattern):
                callback = entry.callback
                cls = getattr(callback, "cls", None)
                if cls is None:
                    continue
                seen[cls.__name__] = cls

    walk(get_resolver())
    return seen


def test_no_viewset_exposes_create_with_an_entirely_read_only_serializer():
    """
    The structural guard.

    A serializer where every field is read-only cannot validate a create — it
    accepts anything, including nothing — so routing `create` to it turns a bad
    request into a database error.
    """
    offenders = []

    for name, cls in _viewsets().items():
        methods = getattr(cls, "http_method_names", None)
        if methods is not None and "post" not in methods:
            continue
        if not hasattr(cls, "create"):
            continue
        # Explicitly closed by `ServiceCreatedOnly`.
        if "create" in getattr(cls, "_closed_writes", ()):
            continue

        # A viewset that writes its OWN `create` is validating the body some
        # other way — `EmployeeViewSet` takes an atomic-creation payload
        # serializer, for instance. Only the INHERITED ModelViewSet.create is
        # dangerous here, because that one feeds the read-only serializer
        # straight to the database.
        writes_own_create = any(
            "create" in base.__dict__
            for base in cls.__mro__
            if base.__module__.startswith(("apps.", "core."))
        )
        if writes_own_create:
            continue

        serializer_class = getattr(cls, "serializer_class", None)
        meta = getattr(serializer_class, "Meta", None)
        if serializer_class is None or meta is None:
            continue

        fields = list(getattr(meta, "fields", []) or [])
        read_only = list(getattr(meta, "read_only_fields", []) or [])
        if not fields:
            continue

        if set(fields) <= set(read_only):
            offenders.append(f"{name} ({serializer_class.__name__})")

    assert not offenders, (
        "These viewsets route `create` to a serializer that cannot validate it, "
        "so an empty POST reaches the database and 500s:\n  "
        + "\n  ".join(offenders)
        + "\n\nClose `create` explicitly (see ExitClearanceItemViewSet) or give "
        "the serializer writable fields."
    )


def test_an_empty_post_never_produces_a_server_error(auth, everyone):
    """
    Behavioural counterpart, walked rather than reasoned about.

    Whatever the cause, a malformed request must produce a 4xx. A 5xx means the
    request reached code that was not expecting it — and the response body then
    tends to describe the schema.
    """
    from tests.cutover.test_ceo_write_walker import ROUTES

    client = auth(everyone["hr_head"])
    crashes = []

    for route in ROUTES:
        for method in ("post", "put", "patch"):
            response = getattr(client, method)(route, {}, format="json")
            if response.status_code >= 500:
                crashes.append(f"{method.upper()} {route} -> {response.status_code}")

    assert not crashes, "Malformed requests produced server errors:\n  " + "\n  ".join(crashes)


def test_clearance_items_cannot_be_created_over_the_api(auth, everyone):
    """
    The specific route the walker caught.

    Clearance items are issued by `_issue_clearance` when an exit starts, copied
    from the template in force. An API-created one would belong to no exit and
    gate nothing.
    """
    response = auth(everyone["hr_head"]).post(
        "/api/v1/exit-clearance-items/", {}, format="json"
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_the_action_routes_on_that_viewset_still_work(auth, everyone, staff):
    """
    Closing `create` must not close `complete` and `waive`.

    Those are the whole point of the viewset accepting POST at all, so this
    asserts the fix was surgical rather than a blanket method removal.
    """
    from apps.offboarding.models import ExitClearanceItem

    # No item exists for this id, so a 404 proves the route is live and reached
    # the lookup — which is all that is being asserted here.
    response = auth(everyone["hr_head"]).post(
        "/api/v1/exit-clearance-items/00000000-0000-0000-0000-000000000000/complete/",
        {},
        format="json",
    )

    assert response.status_code != status.HTTP_405_METHOD_NOT_ALLOWED
    assert response.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST)
