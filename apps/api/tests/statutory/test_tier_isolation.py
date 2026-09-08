"""
META-TEST: Tier 1's evaluation core must have no path to the ORM.

WHAT "TIER 1" MEANS PRECISELY
-----------------------------
`apps.statutory` has two halves, and only one of them is pure:

    PURE (the evaluation core) — must never touch persistence
        apps.statutory.contracts    the boundary types
        apps.statutory.rules.*      the calculation implementations

    ADAPTER (loads rule sets from the database) — necessarily uses the ORM
        apps.statutory.models
        apps.statutory.resolver
        apps.statutory.services.*

The adapter reads a rule set and hands the evaluation core plain data. The core
never reaches back. That is the boundary this test defends.

WHY IT MATTERS
--------------
The same evaluation must serve a payroll run, a salary-offer projection, an
arrears recomputation for a backdated revision, and a cost simulation. Only the
first has a PayrollRun and an Employee. If the core could reach the ORM, the
other three would have to fabricate database rows to compute a number — and,
worse, an evaluation could silently depend on data nobody passed it, which
makes historical reproducibility impossible to guarantee.

HOW IT CHECKS
-------------
Static AST analysis over the **transitive** import graph, not just direct
imports. `contracts.py` importing a helper that imports `django.db` is exactly
the leak this must catch, and a direct-imports-only check would miss it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]

#: Modules whose transitive imports must stay clean.
PURE_MODULES = (
    "apps.statutory.contracts",
    "apps.statutory.rules",
)

#: Anything reaching persistence or the application domain.
FORBIDDEN_PREFIXES = (
    "django.db",
    "django.apps",
    "django.contrib",
    "django.core.cache",
    "rest_framework",
    "celery",
    "apps.employees",
    "apps.payroll",
    "apps.accounts",
    "apps.attendance",
    "apps.leave",
    "apps.organization",
    "apps.audit",
    "core.models",
    "core.middleware",
    # The adapter half of this very app — the core must not reach back into it.
    "apps.statutory.models",
    "apps.statutory.resolver",
    "apps.statutory.services",
)


def _module_path(module: str) -> Path | None:
    """Resolve a dotted module name to a file inside this project."""
    rel = module.replace(".", "/")
    for candidate in (API_ROOT / f"{rel}.py", API_ROOT / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _direct_imports(path: Path, package: str) -> set[str]:
    """Every module this file imports, with relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.add(node.module)
            else:
                # Relative: walk up `level - 1` packages from the current one.
                parts = package.split(".")
                base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                target = ".".join(base + ([node.module] if node.module else []))
                found.add(target)
                # `from . import x` also imports the sibling module.
                for alias in node.names:
                    found.add(f"{target}.{alias.name}" if node.module else
                              ".".join(base + [alias.name]))
    return found


def _package_of(module: str, path: Path) -> str:
    """The package a module lives in, for resolving its relative imports."""
    return module if path.name == "__init__.py" else module.rsplit(".", 1)[0]


def _files_under(module: str) -> list[tuple[str, Path]]:
    """(module_name, path) for a module or every module in a package."""
    path = _module_path(module)
    if path is None:
        return []
    if path.name != "__init__.py":
        return [(module, path)]
    out = []
    package_dir = path.parent
    for py in sorted(package_dir.rglob("*.py")):
        rel = py.relative_to(package_dir).with_suffix("")
        parts = [p for p in rel.parts if p != "__init__"]
        out.append((".".join([module, *parts]) if parts else module, py))
    return out


def _transitive_imports(roots: tuple[str, ...]) -> dict[str, list[str]]:
    """
    Map each reachable project module to the import chain that reached it.

    Only project-local modules are traversed; third-party and stdlib modules are
    recorded but not walked into (we care whether the core *reaches* django.db,
    not what django.db itself imports).
    """
    chains: dict[str, list[str]] = {}
    queue: list[tuple[str, list[str]]] = []

    for root in roots:
        for name, _ in _files_under(root):
            queue.append((name, [name]))
            chains[name] = [name]

    while queue:
        module, chain = queue.pop()
        path = _module_path(module)
        if path is None:
            continue  # external module; recorded by the caller, not walked
        for imported in _direct_imports(path, _package_of(module, path)):
            if imported in chains:
                continue
            chains[imported] = [*chain, imported]
            if _module_path(imported) is not None:
                queue.append((imported, chains[imported]))

    return chains


@pytest.mark.meta
def test_evaluation_core_has_no_import_path_to_orm_or_application_models():
    """
    The core must not reach Django's ORM, DRF, Celery, or any application app —
    directly or through any chain of project modules.
    """
    chains = _transitive_imports(PURE_MODULES)

    violations = [
        (module, chains[module])
        for module in sorted(chains)
        if module.startswith(FORBIDDEN_PREFIXES)
    ]

    if violations:
        lines = [
            "Tier 1's evaluation core reached persistence or the application layer:",
            "",
        ]
        for module, chain in violations:
            lines.append(f"  {module}")
            lines.append(f"    via: {' -> '.join(chain)}")
        lines += [
            "",
            "The core must receive plain data through StatutoryContext and return",
            "plain data in StatutoryAssessment. If it needs something it does not",
            "have, add a field to StatutoryContext — do not reach for the ORM.",
        ]
        pytest.fail("\n".join(lines))


@pytest.mark.meta
def test_statutory_context_keeps_pf_wage_separate_from_gross_wage():
    """
    PF is assessed on Basic + DA, ESI and Professional Tax on full gross.

    Collapsing these into one field is a classic and expensive payroll bug: PF
    computed on gross over-deducts from every employee, every month. The type
    must make passing the wrong one impossible by accident.
    """
    from apps.statutory.contracts import StatutoryContext

    fields = StatutoryContext.__dataclass_fields__
    assert "pf_wage" in fields, "StatutoryContext must expose pf_wage (Basic + DA)."
    assert "gross_wage" in fields, "StatutoryContext must expose gross_wage."
    assert fields["pf_wage"] is not fields["gross_wage"]


@pytest.mark.meta
def test_statutory_context_is_immutable():
    """
    An evaluation must never mutate its own input.

    If it could, replaying a historical payroll run might produce a different
    answer than the original — which defeats the whole reproducibility design.
    """
    import dataclasses
    import datetime as dt
    from decimal import Decimal

    from apps.statutory.contracts import StatutoryContext

    ctx = StatutoryContext(
        period_start=dt.date(2024, 4, 1),
        financial_year="2024-2025",
        period_month=4,
        gross_wage=Decimal("30000.00"),
        pf_wage=Decimal("15000.00"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.gross_wage = Decimal("1.00")  # type: ignore[misc]


@pytest.mark.meta
def test_missing_rule_set_is_a_hard_failure_not_a_default():
    """
    RuleSetMissing must be an exception, never a zero-valued default.

    A missing PF rule set means "we do not know what to deduct". That must stop
    a payroll run, not quietly produce a payslip with no PF on it — an error
    someone notices beats a wrong payslip nobody does.
    """
    import datetime as dt

    from apps.statutory.contracts import RuleSetMissing, StatutoryError

    assert issubclass(RuleSetMissing, StatutoryError)
    assert issubclass(RuleSetMissing, Exception)

    exc = RuleSetMissing("pf", dt.date(2024, 4, 1))
    message = str(exc)
    assert "pf" in message and "2024-04-01" in message, (
        "The error must name the statute and date so finance knows what to configure."
    )
