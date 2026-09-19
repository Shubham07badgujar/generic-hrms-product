"""Cell parsers shared by the platform specs."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_DIGITS = re.compile(r"-?\d+(?:\.\d+)?")
#: "5 years 6 months", "5.5 yrs", "5y 6m"
# Alternatives are ordered LONGEST FIRST. Python's regex alternation is
# first-match, not longest-match, so "y|years" against "8 years 6 months"
# consumes only the "y", leaves "ears 6 months", and silently drops the
# months — turning 8.5 years of experience into 8.
_YEARS_MONTHS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:years|year|yrs|yr|y)?\s*"
    r"(?:(\d+)\s*(?:months|month|mos|mo|m))?",
    re.I,
)
#: Indian salary shorthand, which both platforms emit freely.
_LAKH = re.compile(r"(\d+(?:\.\d+)?)\s*(?:lakh|lakhs|lac|lacs|l)\b", re.I)
_CRORE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:crore|cr)\b", re.I)


def text(value) -> str:
    return "" if value is None else str(value).strip()


def clean_text(value) -> str:
    """
    Text with the export's empty markers stripped.

    Naukri writes "NA" into every cell it has nothing for; storing that as a
    current employer turns a blank into a fact. The markers live on the
    registry so profile extraction and this parser cannot disagree.
    """
    from .registry import EMPTY_MARKERS

    raw = text(value)
    return "" if raw.lower() in EMPTY_MARKERS else raw


def decimal_years(value) -> Decimal | None:
    """
    Experience, from whatever the export felt like writing.

    "5.5", "5 years 6 months", "5y 6m" and Naukri's "5 Year(s) 6 Month(s)"
    all mean the same thing; returning None for anything else — "Fresher",
    "NA" — is safer than guessing, since this feeds a numeric column HR will
    read as fact.
    """
    raw = text(value)
    if not raw:
        return None
    # Naukri's pluralised-parenthesis style: "Year(s)" -> "Years", so the
    # months half is not cut off by the "(s)" the alternation cannot cross.
    raw = re.sub(r"\(s\)", "s", raw, flags=re.I)
    if raw.replace(".", "", 1).isdigit():
        try:
            return Decimal(raw).quantize(Decimal("0.1"))
        except InvalidOperation:
            return None
    match = _YEARS_MONTHS.search(raw)
    if not match:
        return None
    years = Decimal(match.group(1))
    if match.group(2):
        years += Decimal(match.group(2)) / Decimal(12)
    return years.quantize(Decimal("0.1"))


def money(value) -> Decimal | None:
    """
    Expected CTC. Handles lakh/crore shorthand and separators.

    Returns None rather than a wrong number: a mis-parsed salary that silently
    becomes 5 instead of 500000 is worse than an empty field, because nobody
    would question it.
    """
    raw = text(value).replace(",", "")
    if not raw:
        return None

    crore = _CRORE.search(raw)
    if crore:
        return (Decimal(crore.group(1)) * Decimal(10_000_000)).quantize(Decimal("0.01"))
    lakh = _LAKH.search(raw)
    if lakh:
        return (Decimal(lakh.group(1)) * Decimal(100_000)).quantize(Decimal("0.01"))

    found = _DIGITS.search(raw)
    if not found:
        return None
    try:
        amount = Decimal(found.group(0))
    except InvalidOperation:
        return None
    return amount.quantize(Decimal("0.01")) if amount >= 0 else None


def days(value) -> int | None:
    """Notice period. "30", "30 days", "1 month", "Immediate"."""
    raw = text(value).lower()
    if not raw:
        return None
    if "immediate" in raw or raw in {"0", "none", "nil"}:
        return 0
    month = re.search(r"(\d+)\s*month", raw)
    if month:
        return int(month.group(1)) * 30
    found = _DIGITS.search(raw)
    if not found:
        return None
    try:
        parsed = int(float(found.group(0)))
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 3650 else None


#: A real date, written the way a spreadsheet writes one.
_ISO_DATE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_DMY_DATE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")


def iso_date(value):
    """
    A joining date, or None.

    Order matters. `YYYY-MM-DD` is unambiguous and tried first; everything else
    that is all digits is read DAY-FIRST, which is what every Indian HR
    spreadsheet means by `03/04/2025` and what the rest of this product assumes
    (`%d %b %Y` in every letter it renders).

    THE AMBIGUITY IS REAL AND IS NOT RESOLVED HERE. `03/04/2025` is the third
    of April to the person who typed it and the fourth of March to an American
    spreadsheet, and a joining date is not cosmetic: it sets probation, leave
    accrual and the first payroll period. So day-first is applied as the house
    convention AND `is_ambiguous_date()` marks the row, which the employee
    importer turns into a warning the reviewer sees before committing. Guessing
    silently is what makes a wrong date arrive as a fact.

    Returns None rather than raising for anything unrecognised: the caller
    reports a missing required field, which is a better message than a parser
    error about a cell nobody can see.
    """
    import datetime as dt

    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value

    raw = clean_text(value)
    if not raw:
        return None

    match = _ISO_DATE.match(raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        match = _DMY_DATE.match(raw)
        if not match:
            return None
        day, month, year = (int(part) for part in match.groups())

    try:
        return dt.date(year, month, day)
    except ValueError:
        # 31/02/2025 and friends. None, so the row reports a bad date rather
        # than this raising into the middle of a 2,000-row walk.
        return None


def is_ambiguous_date(value) -> bool:
    """
    Whether this cell could be read as two different dates.

    True only for the day-first/month-first overlap -- both parts 12 or under,
    and not written in ISO. `25/12/2025` is unambiguous because there is no
    25th month; `03/04/2025` is not.
    """
    raw = clean_text(value)
    if not raw or _ISO_DATE.match(raw):
        return False
    match = _DMY_DATE.match(raw)
    if not match:
        return False
    first, second, _year = (int(part) for part in match.groups())
    return first <= 12 and second <= 12
