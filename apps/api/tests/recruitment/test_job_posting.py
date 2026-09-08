"""
The job opening as the office posts it: the posting criteria fields, and the
department↔designation rule applied at the FRONT of the pipeline rather than
discovered months later at conversion.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.organization.models import Designation
from apps.recruitment.models import JobOpening
from core.access.catalog import DepartmentKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def ops_designation(org):
    return Designation.objects.create(
        title="Operations Coordinator",
        department=org["departments"][DepartmentKind.OPERATIONS],
    )


def test_a_job_carries_the_posting_criteria(therapist_job):
    """Age limit, gender preference and salary live on the job and round-trip."""
    therapist_job.age_limit = 35
    therapist_job.gender_preference = "female"
    therapist_job.salary = "₹3–4 LPA"
    therapist_job.full_clean(exclude=["created_by", "updated_by"])
    therapist_job.save()

    therapist_job.refresh_from_db()
    assert therapist_job.age_limit == 35
    assert therapist_job.gender_preference == "female"
    assert therapist_job.salary == "₹3–4 LPA"


def test_the_posting_criteria_are_optional_with_safe_defaults(therapist_job):
    """Existing rows and scripted fixtures never specified them — still valid."""
    assert therapist_job.age_limit is None
    assert therapist_job.gender_preference == "any"
    assert therapist_job.salary == ""


def test_a_job_refuses_a_designation_from_another_department(
    therapist_job, ops_designation
):
    """A medical job cannot carry an operations title."""
    therapist_job.designation = ops_designation
    with pytest.raises(ValidationError) as exc:
        therapist_job.full_clean(exclude=["created_by", "updated_by"])
    assert "belongs to another department" in str(exc.value)


def test_a_job_accepts_a_designation_of_its_own_department(therapist_job, org):
    """The matching combination — and one with no department at all — both pass."""
    therapist_job.designation = org["designation"]  # Physiotherapist, MEDICAL
    therapist_job.full_clean(exclude=["created_by", "updated_by"])

    therapist_job.designation = org["any_designation"]  # unassigned title
    therapist_job.full_clean(exclude=["created_by", "updated_by"])


def test_the_serializer_refuses_the_invalid_combination_too(
    therapist_job, ops_designation
):
    """
    The API path: DRF never calls model.full_clean on its own, so the
    serializer's validate() must surface the same refusal.
    """
    from apps.recruitment.api.serializers import JobOpeningSerializer

    serializer = JobOpeningSerializer(
        therapist_job, data={"designation": ops_designation.pk}, partial=True
    )
    assert not serializer.is_valid()
    assert "designation" in serializer.errors


def test_the_serializer_exposes_the_posting_criteria(therapist_job):
    from apps.recruitment.api.serializers import JobOpeningSerializer

    data = JobOpeningSerializer(therapist_job).data
    assert set(["age_limit", "gender_preference", "salary"]) <= set(data)
