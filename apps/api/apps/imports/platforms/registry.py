"""
The adapter contract.

A platform is described, not coded: which headers map to which canonical field,
how each value is parsed, which fields are required, and whether the platform
can be supported at all. The importer reads the spec; it never branches on a
platform name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

#: Canonical fields an adapter may populate. Anything else in the export is
#: carried in `raw` and ignored — WorkIndia ships age and gender, which we have
#: no lawful reason to store against a candidate.
CANONICAL_FIELDS = (
    "external_id",
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "current_employer",
    "total_experience_years",
    "expected_ctc",
    "notice_period_days",
)


@dataclass(frozen=True)
class ColumnSpec:
    """
    One canonical field, and the headers a platform might call it.

    Aliases are matched case- and whitespace-insensitively, because exports
    vary between "Candidate Name", "candidate name" and "Candidate  Name"
    across versions of the same dashboard.
    """

    field: str
    aliases: tuple[str, ...]
    parser: Callable[[str], object] | None = None


#: Cell values that mean "no value" in the wild — Naukri writes "NA" into
#: every empty cell, other exports use their own spellings. Compared
#: case-insensitively against the whole stripped cell.
EMPTY_MARKERS = frozenset(
    {"na", "n/a", "n.a.", "not mentioned", "none", "nil", "-", "_", "--"}
)


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    columns: tuple[ColumnSpec, ...] = ()
    #: Canonical fields without which a row cannot be imported at all. Identity
    #: is checked separately — a row needs a name AND some way to be recognised.
    required: frozenset[str] = frozenset({"first_name"})
    available: bool = True
    #: Shown verbatim in the API and the UI when `available` is False. Says what
    #: is missing and what would unblock it, so nobody has to guess whether the
    #: integration is coming.
    unavailable_reason: str = ""
    notes: str = ""
    #: Extra JOB-RELEVANT columns carried into `Candidate.profile`, keyed by
    #: ColumnSpec.field. Deliberately curated per platform, never "everything
    #: else": protected attributes an export ships anyway (gender, marital
    #: status, date of birth) are not listed here and therefore never leave
    #: the staged file. Values are stored as the export's own text.
    profile_columns: tuple[ColumnSpec, ...] = ()

    def alias_map(self) -> dict[str, str]:
        """normalised header -> canonical field."""
        out: dict[str, str] = {}
        for column in self.columns:
            for alias in column.aliases:
                out[normalise_header(alias)] = column.field
        return out

    def parser_for(self, canonical_field: str):
        for column in self.columns:
            if column.field == canonical_field:
                return column.parser
        return None

    def profile_from_raw(self, raw: dict) -> dict:
        """
        The curated extras of one row, from its original cells.

        Reads `raw` (header -> cell) rather than a parsed row, so it works at
        commit time on rows staged before this platform learned the column —
        and on hand-edited rows, whose raw is the source of truth anyway.
        """
        by_alias: dict[str, ColumnSpec] = {}
        for column in self.profile_columns:
            for alias in column.aliases:
                by_alias[normalise_header(alias)] = column

        out: dict[str, str] = {}
        for header, value in (raw or {}).items():
            column = by_alias.get(normalise_header(header))
            if column is None:
                continue
            text = ("" if value is None else str(value)).strip()
            if not text or text.lower() in EMPTY_MARKERS:
                continue
            out.setdefault(column.field, text)
        return out


def normalise_header(raw: str) -> str:
    """Case-fold, collapse whitespace, drop punctuation that varies by export."""
    text = (raw or "").strip().lower()
    for ch in ("_", "-", ".", "/", "(", ")", "*", ":", ",", "?", "!", "'"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


_REGISTRY: dict[str, PlatformSpec] = {}


def register(spec: PlatformSpec) -> PlatformSpec:
    _REGISTRY[spec.key] = spec
    return spec


def get_platform(key: str) -> PlatformSpec | None:
    return _REGISTRY.get(key)


def list_platforms() -> list[PlatformSpec]:
    """Available ones first, then the rest — both groups alphabetical."""
    return sorted(_REGISTRY.values(), key=lambda s: (not s.available, s.label))
