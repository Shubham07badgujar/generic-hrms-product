"""
Conversion normalises the job's freehand employment type into the Employee
enum — an 'internship' posting must hire an 'intern', not 400.
"""

from types import SimpleNamespace

from apps.employees.models import EmploymentType
from apps.recruitment.services.hiring import _employment_type_for


def test_synonyms_map_into_the_closed_enum():
    cases = {
        "internship": EmploymentType.INTERN,
        "Intern": EmploymentType.INTERN,
        "Full Time": EmploymentType.FULL_TIME,
        "full-time": EmploymentType.FULL_TIME,
        "permanent": EmploymentType.FULL_TIME,
        "part time": EmploymentType.PART_TIME,
        "contract": EmploymentType.CONTRACT,
        "consultant": EmploymentType.CONSULTANT,
        # The safe fallback: unknown wording hires as full-time, correctable.
        "gig": EmploymentType.FULL_TIME,
        "": EmploymentType.FULL_TIME,
    }
    for raw, expected in cases.items():
        assert _employment_type_for(SimpleNamespace(employment_type=raw)) == expected
