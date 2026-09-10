"""
Enumerate the API's own detail routes, from the URL resolver.

Introspection of the running application, in the same spirit as the system
checks next door: a list nobody maintains cannot go stale, and a route added
next year is covered the day it is added rather than the day somebody
remembers it.

This module answers three questions and nothing else -- which routes exist,
which model each one serves, and what a walkable path looks like for a given
id. Deciding WHICH ids to walk, and what an acceptable answer is, belongs to
the caller: the tenancy suite fills routes from a two-organization fixture,
and `scripts/verify_tenant_isolation.py` fills them from three provisioned
organizations. Both ask the same question of the same surface, which is the
point of the shared module -- a divergence between the test that gates the
build and the script that produces the evidence would be the worst kind.
"""

from __future__ import annotations

import re

from django.urls import URLPattern, URLResolver, clear_url_caches, get_resolver


def detail_routes(prefix: str = "api/"):
    """(path template, view class) for every route under `prefix`."""
    found: list[tuple[str, type]] = []

    def walk(resolver, so_far=""):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry, so_far + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                full = so_far + str(entry.pattern)
                if not full.startswith(prefix):
                    continue
                callback = getattr(entry, "callback", None)
                view = getattr(callback, "cls", None) or getattr(
                    callback, "view_class", None
                )
                if view is not None:
                    found.append((full, view))

    walk(get_resolver())
    clear_url_caches()
    return found


def concretize(template: str, pk) -> str | None:
    """
    Turn a route pattern into a walkable path, or None if it is not walkable.

    ORDER MATTERS, and getting it backwards fails silently. DRF's router emits
    `(?P<pk>[^/.]+)`, so a substitution written for Django's `<pk>` converter
    syntax matches the `<pk>` INSIDE that named group, rewriting it to
    `(?PXXXX[^/.]+)` and leaving a pattern that never resolves. The named group
    therefore has to go first. The wrong order produced zero walkable routes
    and would have reported the entire API surface clean.
    """
    if "format" in template:
        return None  # the `.json` suffix variants are the same routes twice
    path = re.sub(r"\(\?P<pk>[^)]*\)", str(pk), template)  # DRF router first
    path = re.sub(r"<[^:>]*:?pk>", str(pk), path)  # then path() converters
    path = path.replace("^", "").replace("$", "")
    if "<" in path or any(ch in path for ch in "[]*+|()?"):
        return None
    return "/" + path


def model_of(view):
    """The model a view serves, or None when it does not declare a queryset."""
    return getattr(getattr(view, "queryset", None), "model", None)


def rows_for(model, organization_id):
    """
    Every row of `model` owned by one organization, escaping the tenant filter.

    `all_orgs()` is the sanctioned escape hatch and exists on both org-owned
    managers. Deliberately-exempt tables keep a plain manager, so the fallback
    is safe only because nothing filters those.
    """
    manager = model.objects
    everything = manager.all_orgs() if hasattr(manager, "all_orgs") else manager.all()
    return everything.filter(organization_id=organization_id)
