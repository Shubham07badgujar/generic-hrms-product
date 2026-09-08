"""
The offer letter PDF: generated from stored data, frozen at send, attached
to the offer email, and previewable before sending.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core import mail
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.organization.models import OrgSettings
from apps.recruitment.letters import offer_letter_pdf
from apps.recruitment.services.hiring import create_offer, send_offer

JOINING = dt.date(2026, 10, 1)

pytestmark = pytest.mark.django_db

def _tiny_png() -> bytes:
    """A real PNG, made by PIL — reportlab embeds it without complaint."""
    import io as _io

    from PIL import Image as PILImage

    buffer = _io.BytesIO()
    PILImage.new("RGBA", (12, 12), (35, 43, 124, 255)).save(buffer, "PNG")
    return buffer.getvalue()


TINY_PNG = _tiny_png()


@pytest.fixture
def selected(therapist_job, make_application, drive_to_selection, staff):
    application = make_application(therapist_job)
    return drive_to_selection(
        application,
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )


@pytest.fixture
def draft_offer(selected, staff, org):
    from core.access.catalog import Layer

    return create_offer(
        application=selected,
        actor=staff["hr_head"].user,
        offered_ctc="600000.00",
        joining_date=JOINING,
        designation=org["designation"],
        level=org["levels"][Layer.STAFF],
        reporting_manager=staff["medical_director"],
        valid_until=dt.date(2026, 9, 20),
    )


def test_the_letter_renders_the_offer_not_hardcoded_text(draft_offer):
    content, filename = offer_letter_pdf(draft_offer)
    assert content[:5] == b"%PDF-"
    assert draft_offer.application.candidate.full_name in filename
    # The visible strings live in content streams; the metadata title is
    # plain text and proves the letter is about THIS candidate.
    assert draft_offer.application.candidate.full_name.encode() in content


def test_the_letter_survives_missing_letterhead(draft_offer):
    """No logo, no signature, no settings row edits — still a valid PDF."""
    OrgSettings.objects.all().delete()
    content, _ = offer_letter_pdf(draft_offer)
    assert content[:5] == b"%PDF-"


def test_send_freezes_the_letter_and_attaches_it(draft_offer, staff, django_capture_on_commit_callbacks):
    org = OrgSettings.objects.first()
    org.signatory_name = "Hema Rao"
    org.signatory_designation = "HR Head"
    org.signature.save("sig.png", ContentFile(TINY_PNG), save=False)
    org.logo.save("logo.png", ContentFile(TINY_PNG), save=False)
    org.save()

    with django_capture_on_commit_callbacks(execute=True):
        send_offer(offer=draft_offer, actor=staff["hr_head"].user)

    draft_offer.refresh_from_db()
    assert draft_offer.letter_pdf, "the sent letter must be stored on the offer"

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert len(message.attachments) == 1
    filename, payload, mimetype = message.attachments[0]
    assert filename.endswith(".pdf")
    assert draft_offer.application.candidate.full_name in filename
    assert mimetype == "application/pdf"
    assert bytes(payload[:5]) == b"%PDF-"


def test_hr_previews_a_draft_and_downloads_the_frozen_sent_letter(
    draft_offer, staff, django_capture_on_commit_callbacks
):
    client = APIClient()
    client.force_authenticate(user=staff["hr_head"].user)

    # Draft: a live preview, nothing stored.
    response = client.get(f"/api/v1/offers/{draft_offer.pk}/letter/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    draft_offer.refresh_from_db()
    assert not draft_offer.letter_pdf

    with django_capture_on_commit_callbacks(execute=True):
        send_offer(offer=draft_offer, actor=staff["hr_head"].user)

    response = client.get(f"/api/v1/offers/{draft_offer.pk}/letter/")
    assert response.status_code == 200
    assert response.content[:5] == b"%PDF-"


def test_outsiders_cannot_fetch_offer_letters(draft_offer, make_user, roles):
    client = APIClient()
    client.force_authenticate(user=make_user("therapist", email="rank@x.test"))
    response = client.get(f"/api/v1/offers/{draft_offer.pk}/letter/")
    assert response.status_code in (403, 404)


def test_org_settings_surface_is_admins_and_audited(make_user, roles, org):
    from apps.audit.models import AuditLog

    admin = make_user("admin", email="root@x.test")
    hr_head = make_user("hr_head", email="hrh@x.test")

    client = APIClient()
    client.force_authenticate(user=hr_head)
    assert client.get("/api/v1/org/settings/").status_code == 403

    client.force_authenticate(user=admin)
    response = client.get("/api/v1/org/settings/")
    assert response.status_code == 200

    response = client.patch("/api/v1/org/settings/", {
        "signatory_name": "Hema Rao",
        "signatory_designation": "HR Head",
    }, format="json")
    assert response.status_code == 200
    assert response.data["signatory_name"] == "Hema Rao"
    assert AuditLog.objects.filter(
        entity_type="organization.OrgSettings",
        after__signatory_name="Hema Rao",
    ).exists()


# ---------------------------------------------------- CTC-conditional clause


def _pdf_text(content: bytes) -> str:
    """Decompress the PDF's content streams so clause text is searchable."""
    import re
    import zlib

    parts = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
        raw = match.group(1).strip()
        # reportlab default: ASCII85 over Flate.
        if raw.endswith(b"~>"):
            import base64

            try:
                raw = base64.a85decode(raw, adobe=True)
            except Exception:
                pass
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass  # stream stored uncompressed — use as-is
        parts.append(raw.decode("latin-1", "replace"))
    return "".join(parts)


def test_the_ctc_decides_the_security_deposit_clause(selected, staff, org):
    from apps.recruitment.letters import includes_security_deposit

    # The rule itself, at the boundary HR specified.
    assert includes_security_deposit("168000.00") is False
    assert includes_security_deposit("240000.00") is False   # exactly: excluded
    assert includes_security_deposit("240000.01") is True
    assert includes_security_deposit("300000.00") is True

    from core.access.catalog import Layer

    offer = create_offer(
        application=selected,
        actor=staff["hr_head"].user,
        offered_ctc="300000.00",
        joining_date=JOINING,
        designation=org["designation"],
        level=org["levels"][Layer.STAFF],
        reporting_manager=staff["medical_director"],
    )
    content, _ = offer_letter_pdf(offer)
    text = _pdf_text(content)
    assert "Security Deposit Policy" in text
    assert "first month's" in text

    offer.offered_ctc = "168000.00"
    offer.save(update_fields=["offered_ctc"])
    low, _ = offer_letter_pdf(offer)
    assert "Security Deposit Policy" not in _pdf_text(low)

    offer.offered_ctc = "240000.00"
    offer.save(update_fields=["offered_ctc"])
    exact, _ = offer_letter_pdf(offer)
    assert "Security Deposit Policy" not in _pdf_text(exact)
