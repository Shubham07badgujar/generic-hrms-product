"""
Loading and validating golden-master fixtures.

Deliberately lives under `tests/`, not in the application: fixtures are the
specification the implementation is measured against, and specification code
that ships alongside the thing it specifies invites the two drifting together.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "statutory"
RATE_SET_ROOT = FIXTURE_ROOT / "rate_sets"
CASE_ROOT = FIXTURE_ROOT / "cases"

CATEGORIES = frozenset({"normal", "boundary", "exemption", "edge"})


@dataclass(frozen=True)
class RateSet:
    key: str                      # "pf/2024-04-01"
    statute: str
    rule_version: str
    parameters: dict[str, Any]
    verification_status: str      # UNVERIFIED | VERIFIED
    source_citation: str
    assumptions: list[str]
    jurisdiction: str = ""
    regime: str = ""

    @property
    def is_verified(self) -> bool:
        return self.verification_status.upper() == "VERIFIED"

    @property
    def parameter_names(self) -> set[str]:
        """Flattened parameter names, including nested ones as dotted paths."""
        return _flatten_keys(self.parameters)


#: Sentinel for an expected value that cannot be determined until a statutory
#: question is answered. A case carrying this must never be asserted as passing —
#: asserting either candidate value would be picking an answer the organisation
#: has not chosen.
DISPUTED = "DISPUTED"


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    rule_set_key: str
    covers: tuple[str, ...]
    source: str
    assumptions: tuple[str, ...]
    given: dict[str, Any]
    expect: dict[str, Any]
    derivation: str
    path: Path
    disputed: bool = False
    disputes: tuple[str, ...] = ()

    @property
    def statute(self) -> str:
        return self.rule_set_key.split("/")[0]

    @property
    def disputed_fields(self) -> tuple[str, ...]:
        """Expected values still awaiting a statutory decision."""
        return tuple(k for k, v in self.expect.items() if v == DISPUTED)

    @property
    def settled_expectations(self) -> dict[str, Any]:
        """
        The part of this case that CAN be asserted today.

        A disputed case is not worthless — the undisputed lines still test the
        engine. Only the contested values are withheld.
        """
        return {k: v for k, v in self.expect.items() if v != DISPUTED}


def _flatten_keys(data: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        out.add(path)
        if isinstance(value, dict):
            out |= _flatten_keys(value, f"{path}.")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            # e.g. slabs / exemptions: expose the list itself, not each row.
            pass
    return out


@lru_cache(maxsize=1)
def load_rate_sets() -> dict[str, RateSet]:
    sets: dict[str, RateSet] = {}
    for path in sorted(RATE_SET_ROOT.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        key = str(path.relative_to(RATE_SET_ROOT).with_suffix("")).replace("\\", "/")
        verification = raw.get("verification") or {}
        source = raw.get("source") or {}
        sets[key] = RateSet(
            key=key,
            statute=raw["statute"],
            rule_version=raw["rule_version"],
            parameters=raw.get("parameters") or {},
            verification_status=str(verification.get("status", "UNVERIFIED")),
            source_citation=str(source.get("citation", "")),
            assumptions=list(raw.get("assumptions") or []),
            jurisdiction=raw.get("jurisdiction") or "",
            regime=raw.get("regime") or "",
        )
    return sets


@lru_cache(maxsize=1)
def load_cases() -> tuple[Case, ...]:
    cases: list[Case] = []
    for path in sorted(CASE_ROOT.rglob("*.yaml")):
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for row in rows:
            cases.append(
                Case(
                    id=row["id"],
                    category=row["category"],
                    rule_set_key=row["rule_set"],
                    covers=tuple(row.get("covers") or ()),
                    source=str(row.get("source", "")),
                    assumptions=tuple(row.get("assumptions") or ()),
                    given=row.get("given") or {},
                    expect=row.get("expect") or {},
                    derivation=str(row.get("derivation", "")),
                    path=path,
                    disputed=bool(row.get("disputed", False)),
                    disputes=tuple(row.get("disputes") or ()),
                )
            )
    return tuple(cases)


def decimalise(value: Any) -> Any:
    """
    YAML numerics are strings in these fixtures, on purpose.

    A bare `1800.00` in YAML parses as a float, and float(1800.00) is not
    exactly 1800.00 — money comparisons would then fail for reasons that have
    nothing to do with the statute. Quoting forces exact Decimal construction.
    """
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        raise TypeError(
            f"Fixture money value {value!r} must be quoted in YAML so it becomes an "
            f"exact Decimal. Bare numerics are parsed as floats and lose precision."
        )
    return value


# ---------------------------------------------------------------------------
# Disputes — open statutory interpretation questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dispute:
    id: str
    statute: str
    status: str                # open | resolved
    blocking: tuple[str, ...]  # rate-set keys that cannot be verified
    question: str
    impact: str
    how_to_resolve: str
    resolution: dict | None

    @property
    def is_open(self) -> bool:
        return self.status.lower() == "open"


@lru_cache(maxsize=1)
def load_disputes() -> tuple[Dispute, ...]:
    path = FIXTURE_ROOT / "disputes.yaml"
    if not path.exists():
        return ()
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return tuple(
        Dispute(
            id=row["id"],
            statute=row["statute"],
            status=str(row.get("status", "open")),
            blocking=tuple(row.get("blocking") or ()),
            question=str(row.get("question", "")).strip(),
            impact=str(row.get("impact", "")).strip(),
            how_to_resolve=str(row.get("how_to_resolve", "")).strip(),
            resolution=row.get("resolution"),
        )
        for row in rows
    )


def open_disputes_for(rate_set_key: str) -> list[Dispute]:
    return [d for d in load_disputes() if d.is_open and rate_set_key in d.blocking]
