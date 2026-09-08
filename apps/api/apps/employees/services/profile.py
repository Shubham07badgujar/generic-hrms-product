"""
The parts of a profile its owner may maintain.

Separate from `creation` (which places a person in the organisation) and from
`lifecycle` (which changes their standing in it). What lives here is the
person's own description of themselves — the fields where the employee, not
HR, is the authority on what is correct.

A name change is audited like any other write to the employee record. It is
not a trivial edit: the name appears on payslips, offer letters and the audit
trail itself, so "who changed this, when, and from what" has to be answerable.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

from core.access import Resource

#: Letters (any script — the clinic employs people whose names are not Latin),
#: spaces, and the three punctuation marks that appear in real names: the
#: hyphen in double-barrelled names, the apostrophe in O'Brien, and the full
#: stop in an initial. Digits and markup are refused: a name is not a field
#: where either belongs, and refusing them here keeps them out of every letter
#: and payslip the name is printed on.
NAME_PATTERN = re.compile(r"^[^\W\d_][^\d<>{}\[\]|\\/@]*$", re.UNICODE)

MAX_NAME_LENGTH = 100


def _clean(value: str | None, *, field: str, required: bool) -> str:
    """Normalise one name part, or explain why it cannot be used."""
    text = " ".join((value or "").split())  # collapse runs of whitespace
    label = field.replace("_", " ").capitalize()

    if not text:
        if required:
            raise ValidationError({field: f"{label} cannot be empty."})
        return ""

    if len(text) > MAX_NAME_LENGTH:
        raise ValidationError(
            {field: f"{label} cannot be longer than {MAX_NAME_LENGTH} characters."}
        )
    if not NAME_PATTERN.match(text):
        raise ValidationError(
            {
                field: (
                    f"{label} may contain letters, spaces, hyphens, apostrophes "
                    f"and full stops. It cannot contain digits or symbols."
                )
            }
        )
    return text


def rename_employee(
    *, employee, first_name: str, middle_name: str = "", last_name: str = "", actor,
    reason: str = "",
):
    """
    Correct or update the name on an employee record.

    Used both by HR fixing a misspelling at data entry and by the employee
    themselves after a marriage, a legal change, or simply because the office
    spelled it wrong on day one. The rules are the same either way; who may
    reach WHICH employee is decided by the permission scope at the API edge,
    not here.

    `full_name` is derived from these three fields rather than stored, so
    nothing downstream needs updating — the next payslip, letter and directory
    listing all read the new name automatically.
    """
    from apps.audit.events import record_event

    first = _clean(first_name, field="first_name", required=True)
    middle = _clean(middle_name, field="middle_name", required=False)
    last = _clean(last_name, field="last_name", required=False)

    before = {
        "first_name": employee.first_name,
        "middle_name": employee.middle_name,
        "last_name": employee.last_name,
        "full_name": employee.full_name,
    }
    if (first, middle, last) == (
        employee.first_name, employee.middle_name, employee.last_name
    ):
        return employee  # nothing changed; do not write an empty audit row

    employee.first_name = first
    employee.middle_name = middle
    employee.last_name = last
    employee.updated_by = actor
    employee.save(
        update_fields=["first_name", "middle_name", "last_name", "updated_by", "updated_at"]
    )

    record_event(
        employee,
        actor=actor,
        entity_type="employees.Employee",
        verb="update",
        resource=Resource.EMPLOYEE,
        before=before,
        after={
            "first_name": employee.first_name,
            "middle_name": employee.middle_name,
            "last_name": employee.last_name,
            "full_name": employee.full_name,
        },
        reason=reason or f"Name changed to {employee.full_name}",
    )
    return employee
