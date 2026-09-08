"""
Reading rule-set parameters safely.

A rule set is data, so a rule can be handed parameters it does not understand
or is missing ones it requires. Every access goes through here so the failure
is `ParametersInvalid` naming the rule and the parameter, rather than a
KeyError, a None propagating into arithmetic, or — worst — a silent default
that produces a plausible but wrong deduction.

There is deliberately no `default=` on the required readers. A statutory
parameter that can fall back to a default is a statutory parameter nobody has
to configure, which is how the previous system ran a year of payroll on
"TODO: verify" values.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ..contracts import ParametersInvalid


def _fail(rule: str, name: str, detail: str) -> ParametersInvalid:
    return ParametersInvalid(f"{rule}: parameter '{name}' {detail}")


def decimal(params: Mapping[str, Any], name: str, *, rule: str) -> Decimal:
    """A required decimal parameter."""
    if name not in params or params[name] is None:
        raise _fail(rule, name, "is required but missing from the rule set.")
    try:
        return Decimal(str(params[name]))
    except (InvalidOperation, ValueError) as exc:
        raise _fail(rule, name, f"is not a valid decimal: {params[name]!r}") from exc


def optional_decimal(params: Mapping[str, Any], name: str, *, rule: str) -> Decimal | None:
    """
    A parameter that is meaningfully absent.

    Distinct from a missing required one: `None` here means "this rule set says
    the constraint does not apply", which is a statement, not an omission.
    """
    if name not in params or params[name] is None:
        return None
    return decimal(params, name, rule=rule)


def flag(params: Mapping[str, Any], name: str, *, rule: str) -> bool:
    if name not in params or params[name] is None:
        raise _fail(rule, name, "is required but missing from the rule set.")
    if not isinstance(params[name], bool):
        raise _fail(rule, name, f"must be true or false, got {params[name]!r}")
    return params[name]


def rows(params: Mapping[str, Any], name: str, *, rule: str) -> list[Mapping[str, Any]]:
    """A required list-of-mappings parameter such as `slabs` or `bands`."""
    value = params.get(name)
    if not isinstance(value, list) or not value:
        raise _fail(rule, name, "must be a non-empty list.")
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise _fail(rule, name, f"entry {index} must be a mapping, got {row!r}")
    return list(value)


def mapping(params: Mapping[str, Any], name: str, *, rule: str) -> Mapping[str, Any]:
    """
    A required mapping parameter.

    An EMPTY mapping is valid and meaningful — `deduction_caps: {}` in the new
    tax regime means "no Chapter VI-A deduction is allowed", which is very
    different from the key being absent.
    """
    if name not in params or params[name] is None:
        raise _fail(rule, name, "is required but missing from the rule set.")
    if not isinstance(params[name], Mapping):
        raise _fail(rule, name, f"must be a mapping, got {params[name]!r}")
    return params[name]
