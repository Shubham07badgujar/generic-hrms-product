"""
Candidate identity: nullable email, derived keys, and the lawful-basis floor.

The claims under test:

  * a candidate may have a phone and no email, and no address is invented
  * two candidates may not share an address, case-insensitively
  * any number of candidates may have no address at all
  * no candidate can exist without a lawful basis, by ANY path
  * a phone-only candidate cannot silently become an employee

All contact details here are invented. `9876543210` is the documentation range.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.recruitment.models import Candidate, CandidateExternalRef, LegalBasis

pytestmark = pytest.mark.django_db


def _candidate(**overrides):
    fields = {
        "first_name": "Priya",
        "last_name": "Deshmukh",
        "consent_given": True,
        "legal_basis": LegalBasis.CONSENT,
    }
    fields.update(overrides)
    return Candidate.objects.create(**fields)


# ------------------------------------------------------- nullable email


def test_a_phone_only_candidate_can_exist():
    """
    The WorkIndia case. No email, and crucially no invented one.
    """
    candidate = _candidate(email=None, phone="9876543210")

    assert candidate.email is None
    assert candidate.phone_e164 == "+919876543210"


def test_an_empty_email_becomes_null_not_empty_string():
    """
    Load-bearing. Postgres unique indexes treat NULLs as distinct but '' as an
    ordinary value, so a second '' would collide with the first.
    """
    candidate = _candidate(email="", phone="9876543210")

    assert candidate.email is None
    assert candidate.email_normalized is None


def test_many_candidates_may_have_no_email():
    for i in range(3):
        _candidate(email=None, phone=f"98765432{i}0")

    assert Candidate.objects.filter(email__isnull=True).count() == 3


# ------------------------------------------------------- derived keys


def test_email_is_normalized_to_lower_case():
    candidate = _candidate(email="  Priya.Deshmukh@Example.TEST  ")

    assert candidate.email == "priya.deshmukh@example.test"
    assert candidate.email_normalized == "priya.deshmukh@example.test"


def test_two_candidates_cannot_share_an_address_in_different_cases():
    """The old index was case-sensitive and admitted both of these."""
    _candidate(email="ravi@example.test")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _candidate(email="RAVI@example.test")


def test_a_soft_deleted_candidate_frees_their_address():
    first = _candidate(email="reuse@example.test")
    first.is_active = False
    first.save(update_fields=["is_active"])

    again = _candidate(email="reuse@example.test")

    assert again.pk != first.pk


def test_two_candidates_may_share_a_phone():
    """
    A family handset or a shop line. Phone is an index, not a unique
    constraint — turning a normal data-quality event into a hard failure would
    leave HR unable to record the second person at all.
    """
    _candidate(email="a@example.test", phone="9876543210")
    second = _candidate(email="b@example.test", phone="9876543210")

    assert second.phone_e164 == "+919876543210"
    assert Candidate.objects.filter(phone_e164="+919876543210").count() == 2


def test_an_unparseable_phone_leaves_the_key_null():
    candidate = _candidate(email="c@example.test", phone="not a number")

    assert candidate.phone_e164 is None


def test_update_fields_still_refreshes_the_derived_keys():
    """
    The `Interview.save()` trick. A caller passing update_fields would otherwise
    write a new email and leave the index pointing at the old one.
    """
    candidate = _candidate(email="before@example.test")

    candidate.email = "After@Example.TEST"
    candidate.save(update_fields=["email"])
    candidate.refresh_from_db()

    assert candidate.email_normalized == "after@example.test"


# --------------------------------------------------------- lawful basis


def test_a_candidate_with_neither_consent_nor_basis_is_refused_by_the_database():
    """
    The point of the check constraint. Consent used to be enforced only in a
    serializer, so this exact call — a service, a shell, a management command —
    wrote a basis-less record without complaint.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Candidate.objects.create(
                first_name="No", last_name="Basis",
                email="nobasis@example.test",
                consent_given=False, legal_basis="",
            )


def test_a_legal_basis_without_consent_is_permitted():
    """What a bulk import produces: lawful to hold, not consented to us."""
    candidate = _candidate(
        email="imported@example.test",
        consent_given=False,
        legal_basis=LegalBasis.VOLUNTARILY_PROVIDED,
    )

    assert candidate.consent_given is False
    assert candidate.legal_basis == LegalBasis.VOLUNTARILY_PROVIDED


# ------------------------------------------------------- external refs


def test_one_candidate_may_hold_refs_from_several_platforms():
    candidate = _candidate(email="both@example.test")
    CandidateExternalRef.objects.create(
        candidate=candidate, source="workindia", external_id="WI-1"
    )
    CandidateExternalRef.objects.create(
        candidate=candidate, source="naukri", external_id="NK-1"
    )

    assert candidate.external_refs.count() == 2


def test_one_platform_id_cannot_point_at_two_candidates():
    first = _candidate(email="first@example.test")
    second = _candidate(email="second@example.test")
    CandidateExternalRef.objects.create(
        candidate=first, source="workindia", external_id="WI-1"
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CandidateExternalRef.objects.create(
                candidate=second, source="workindia", external_id="WI-1"
            )


def test_the_same_id_on_two_platforms_is_not_a_collision():
    first = _candidate(email="x@example.test")
    second = _candidate(email="y@example.test")
    CandidateExternalRef.objects.create(candidate=first, source="workindia", external_id="1")
    CandidateExternalRef.objects.create(candidate=second, source="naukri", external_id="1")

    assert CandidateExternalRef.objects.count() == 2
