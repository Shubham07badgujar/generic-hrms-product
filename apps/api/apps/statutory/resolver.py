"""
Rule-set resolution by effective date.

Exactly one rule set, or a hard failure. There is deliberately no fallback to
"the most recent" and no implicit zero: a missing PF rule set means "we do not
know what to deduct", which must stop a payroll run rather than quietly produce
a payslip with no PF on it.
"""

from __future__ import annotations

import logging
from datetime import date

from django.conf import settings

from .contracts import RuleSetAmbiguous, RuleSetMissing, RuleSetRef, Statute
from .models import StatutoryRuleSet, VerificationStatus

logger = logging.getLogger("hrms.statutory")


def resolve(
    statute: str,
    *,
    on_date: date,
    jurisdiction: str = "",
    regime: str = "",
    financial_year: str = "",
    require_verified: bool = True,
) -> StatutoryRuleSet:
    """
    The rule set in force for `statute` on `on_date`.

    `require_verified=True` is the production posture. Dev and staging may pass
    False (or set STATUTORY_ALLOW_UNVERIFIED); production refuses to boot with
    that flag set, so the escape hatch cannot follow code into production.
    """
    statuses = [VerificationStatus.VERIFIED, VerificationStatus.SUPERSEDED]
    if not require_verified:
        statuses += [VerificationStatus.DRAFT, VerificationStatus.PENDING]

    qs = StatutoryRuleSet.objects.filter(
        statute=statute,
        jurisdiction=jurisdiction,
        regime=regime,
        is_active=True,
        verification_status__in=statuses,
        effective_from__lte=on_date,
    ).filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=on_date))

    # Income tax is keyed by financial year as well as date: a mid-year Finance
    # Act applies to the whole year, so the date window alone is not decisive.
    if financial_year:
        qs = qs.filter(financial_year=financial_year)

    matches = list(qs.order_by("-effective_from")[:2])

    if not matches:
        raise RuleSetMissing(statute, on_date, jurisdiction, regime)

    if len(matches) > 1 and matches[0].effective_from == matches[1].effective_from:
        # The DB exclusion constraint should make this unreachable. If it fires,
        # that constraint has been dropped or the data predates it.
        raise RuleSetAmbiguous(
            f"Multiple rule sets in force for '{statute}' on {on_date}: "
            f"{matches[0].pk}, {matches[1].pk}. The exclusion constraint "
            f"'excl_ruleset_no_overlapping_in_force' should have prevented this."
        )

    rule_set = matches[0]

    if require_verified and not rule_set.is_usable_for_payroll:
        reason = (
            "its parameters changed after verification"
            if rule_set.is_tampered
            else f"its status is '{rule_set.verification_status}'"
        )
        raise RuleSetMissing(statute, on_date, jurisdiction, regime).with_traceback(None) from (
            RuntimeError(f"Rule set {rule_set.pk} is not usable: {reason}")
        )

    return rule_set


def to_ref(rule_set: StatutoryRuleSet) -> RuleSetRef:
    """Freeze a rule set into the provenance record stored on a payroll run."""
    return RuleSetRef(
        rule_set_id=str(rule_set.pk),
        statute=rule_set.statute,
        rule_version=rule_set.rule_version,
        checksum=rule_set.checksum,
        effective_from=rule_set.effective_from.isoformat(),
        jurisdiction=rule_set.jurisdiction,
        regime=rule_set.regime,
        verification_status=rule_set.verification_status,
        verified_by=getattr(rule_set.verified_by, "email", "") or "",
        verified_at=rule_set.verified_at.isoformat() if rule_set.verified_at else "",
    )


def required_statutes(*, state: str, regime: str, financial_year: str) -> list[dict]:
    """
    Which rule sets a payroll run needs, so the hard gate can report every
    missing or unverified one at once rather than failing on the first.
    """
    needed = [
        {"statute": Statute.PF},
        {"statute": Statute.ESI},
        {"statute": Statute.GRATUITY},
        {"statute": Statute.INCOME_TAX, "regime": regime, "financial_year": financial_year},
    ]
    if state:
        needed.append({"statute": Statute.PROFESSIONAL_TAX, "jurisdiction": state})
    return needed


def unverified_blockers(*, on_date: date, state: str, regime: str, financial_year: str) -> list[str]:
    """
    Human-readable list of what stands between this run and a live payroll.

    Returns every problem, not the first — finance should be able to fix them
    in one pass instead of discovering them one failed run at a time.
    """
    problems: list[str] = []
    for spec in required_statutes(state=state, regime=regime, financial_year=financial_year):
        try:
            resolve(on_date=on_date, require_verified=True, **spec)
        except RuleSetMissing:
            try:
                draft = resolve(on_date=on_date, require_verified=False, **spec)
                problems.append(
                    f"{spec['statute']}"
                    f"{'/' + spec['jurisdiction'] if spec.get('jurisdiction') else ''}: "
                    f"status is '{draft.verification_status}' — must be verified by Finance."
                )
            except RuleSetMissing:
                problems.append(
                    f"{spec['statute']}"
                    f"{'/' + spec['jurisdiction'] if spec.get('jurisdiction') else ''}: "
                    f"no rule set configured for {on_date.isoformat()}."
                )
        except RuleSetAmbiguous as exc:
            problems.append(str(exc))
    return problems


def allow_unverified() -> bool:
    """Dev/staging escape hatch. `prod.py` asserts this is False."""
    return bool(getattr(settings, "STATUTORY_ALLOW_UNVERIFIED", False))


from django.db import models  # noqa: E402
