"""
META-TEST: every scoper applies the tenant predicate.

`docs/ARCHITECTURE.md` states that `scope_queryset()` is the only sanctioned
path to a scoped queryset and that scope filters are never hand-rolled. That is
not actually true: five places decide row visibility, and four of them are
hand-written. Each ends with the same shape --

    if scope == Scope.ALL:
        return queryset

-- which, before the tenant predicate, meant "the whole table" rather than
"the whole organization". Four independent chances to leak, and the rule
forbidding them is a sentence in a document.

So the rule gets a test. Any site branching on `Scope.ALL` must also route its
queryset through `apply_org_predicate`, and a new one cannot appear without
this failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.meta

APPS_ROOT = Path(__file__).resolve().parents[2]

#: Files that decide row visibility by branching on Scope.ALL. Each must apply
#: the tenant predicate to the queryset it returns.
SCOPE_ALL = re.compile(r"scope\s*==\s*Scope\.ALL")
PREDICATE = re.compile(r"apply_org_predicate")


def _python_files():
    for base in ("apps", "core"):
        for path in (APPS_ROOT / base).rglob("*.py"):
            if "__pycache__" in path.parts or "migrations" in path.parts:
                continue
            yield path


def test_every_scope_all_branch_also_applies_the_tenant_predicate():
    offenders = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if SCOPE_ALL.search(text) and not PREDICATE.search(text):
            offenders.append(str(path.relative_to(APPS_ROOT)))

    assert not offenders, (
        "These decide row visibility from Scope.ALL without applying the "
        "tenant predicate, so at Scope.ALL they return the whole table rather "
        "than the whole organization:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_scan_actually_finds_the_scopers():
    """
    Guards the guard. A path glob that silently matched nothing would make the
    assertion above pass forever while checking no files at all.
    """
    found = [p for p in _python_files() if SCOPE_ALL.search(p.read_text(encoding="utf-8"))]

    assert len(found) >= 5, (
        f"Expected at least the five known scopers, found {len(found)}. "
        "File collection is probably broken."
    )
