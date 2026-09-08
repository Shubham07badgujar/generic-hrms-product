"""
The Tier 1 evaluation core: statutory calculation, and nothing else.

Every module under here is pure Python over `contracts` types. No ORM, no DRF,
no settings — enforced by `tests/statutory/test_tier_isolation.py`, which walks
the transitive import graph and fails on any path to persistence.

Rules are addressed by VERSION, not by statute. A rule set names the
implementation it was written for (`pt.v2`), so changing how a statute is
computed means shipping a new version and pointing new rule sets at it — while
historical runs keep resolving the version they were computed under. Replaying
a 2024 payroll in 2027 must reproduce the 2024 answer, and it cannot do that if
"the PF rule" silently means whatever the latest code does.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ..contracts import (
    ESIResult,
    GratuityResult,
    PFResult,
    PTResult,
    RuleVersionUnknown,
    Statute,
    StatutoryContext,
    TDSResult,
)
from . import esi, gratuity, income_tax, pf, pt

#: rule_version -> (statute, implementation)
REGISTRY: dict[str, tuple[str, Callable[..., Any]]] = {
    pf.RULE: (Statute.PF, pf.evaluate),
    esi.RULE: (Statute.ESI, esi.evaluate),
    pt.RULE: (Statute.PROFESSIONAL_TAX, pt.evaluate),
    gratuity.RULE: (Statute.GRATUITY, gratuity.evaluate),
    income_tax.RULE: (Statute.INCOME_TAX, income_tax.evaluate),
}


def implementation(rule_version: str, *, statute: str = "") -> Callable[..., Any]:
    """
    The implementation a rule set names, or a hard failure.

    Unknown versions raise rather than falling back to the newest: data restored
    from a later release must stop payroll, not be computed under rules it was
    never written for.
    """
    if rule_version not in REGISTRY:
        raise RuleVersionUnknown(
            f"Rule set names implementation '{rule_version}', which this release does "
            f"not provide. Known versions: {', '.join(sorted(REGISTRY))}."
        )
    registered_statute, evaluate = REGISTRY[rule_version]
    if statute and statute != registered_statute:
        raise RuleVersionUnknown(
            f"Rule set for statute '{statute}' names implementation '{rule_version}', "
            f"which computes '{registered_statute}'."
        )
    return evaluate


def evaluate(
    statute: str,
    rule_version: str,
    ctx: StatutoryContext,
    parameters: Mapping[str, Any],
    **extra: Any,
) -> PFResult | ESIResult | PTResult | GratuityResult | TDSResult:
    """Dispatch one statute's evaluation to the version its rule set names."""
    return implementation(rule_version, statute=statute)(ctx, parameters, **extra)


__all__ = [
    "REGISTRY",
    "esi",
    "evaluate",
    "gratuity",
    "implementation",
    "income_tax",
    "pf",
    "pt",
]
