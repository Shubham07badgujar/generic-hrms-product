"""
No queryset is built while a module is being imported.

`queryset = Asset.objects.select_related("category")` in a class body runs at
import: no request, no organization, and the result is reused for the life of
the process. DRF clones it per request with `.all()`, and a clone never returns
to the manager. So such a queryset is permanently outside manager-level
tenancy.

That was invisible while the manager did not filter. The first app switched to
a filtering manager turned it into an import-time raise that took down the
URLconf, and with it `manage.py check`, the test collector and any server boot.
`core.querysets.deferred` replaced all 75 of them; this test is what stops the
76th from being written.

The scan is deliberately broader than the crash: it flags ANY manager call
evaluated outside a function body, including `all_orgs()`, which does not raise
but is just as permanently unscoped.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.meta

#: What the conversion left behind. The count may grow; a sharp drop means
#: either sites were deleted or the detector stopped matching the codebase.
KNOWN_DEFERRED_SITES = 75

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _root_model(call: ast.Call) -> str | None:
    """The model name if this call chain is rooted at `<Name>.objects.<method>`."""
    cur: ast.AST = call
    while True:
        if isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Attribute):
            value = cur.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "objects"
                and isinstance(value.value, ast.Name)
            ):
                return value.value.id
            cur = value
        else:
            return None


class _Scanner(ast.NodeVisitor):
    """Manager calls reached without entering a function body."""

    def __init__(self, label: str):
        self.label = label
        self.depth = 0
        self.hits: list[tuple[str, int, str]] = []

    def _function(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def visit_Call(self, node):
        if self.depth == 0:
            model = _root_model(node)
            if model:
                self.hits.append((self.label, node.lineno, model))
                # Keep descending into the ARGUMENTS so a nested manager call
                # is not hidden by the chain that contains it; do not descend
                # into the chain's own calls, which would report it twice.
                for argument in list(node.args) + [k.value for k in node.keywords]:
                    self.visit(argument)
                return
        self.generic_visit(node)


def _scan_source(source: str, label: str) -> list[tuple[str, int, str]]:
    scanner = _Scanner(label)
    scanner.visit(ast.parse(source))
    return scanner.hits


def _project_files():
    for base in ("apps", "core"):
        for path in sorted((ROOT / base).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/migrations/" in relative or "__pycache__" in relative:
                continue
            if relative == "core/querysets.py":
                continue  # defines the replacement
            yield relative, path


def test_the_scanner_catches_a_violation_when_one_exists():
    """
    Guards the guard.

    A detector that silently stopped matching would report a clean codebase
    forever, which is exactly the failure mode this file exists to prevent.
    """
    offending = (
        "class View:\n"
        "    queryset = Asset.objects.select_related('category')\n"
    )
    nested = (
        "class View:\n"
        "    queryset = Asset.objects.filter(pk__in=Other.objects.values('id'))\n"
    )
    inside_a_function = (
        "def get_queryset(self):\n"
        "    return Asset.objects.select_related('category')\n"
    )

    assert _scan_source(offending, "probe") == [("probe", 2, "Asset")]
    assert {hit[2] for hit in _scan_source(nested, "probe")} == {"Asset", "Other"}
    assert _scan_source(inside_a_function, "probe") == []


def test_no_module_builds_a_queryset_at_import():
    violations = []
    for label, path in _project_files():
        violations.extend(_scan_source(path.read_text(encoding="utf-8"), label))

    assert not violations, (
        "these evaluate a manager while the module is imported, so the tenant "
        "predicate runs with nothing bound or never runs at all. Use "
        "core.querysets.deferred: "
        + ", ".join(f"{label}:{line} ({model})" for label, line, model in violations)
    )


def test_the_deferred_replacement_is_actually_in_use():
    """
    The other direction.

    Zero violations would also be reported by a codebase that had deleted every
    class-level queryset, or by a scan pointed at the wrong tree.
    """
    uses = 0
    for _, path in _project_files():
        uses += path.read_text(encoding="utf-8").count("deferred(")

    assert uses >= KNOWN_DEFERRED_SITES, (
        f"found {uses} uses of deferred(), expected at least "
        f"{KNOWN_DEFERRED_SITES}. Were class-level querysets removed, or did "
        f"the scan lose its way?"
    )
