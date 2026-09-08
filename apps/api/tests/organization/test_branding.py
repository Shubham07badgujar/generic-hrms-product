"""
The organisation's public face.

The one endpoint the frontend learns its identity from — public by design,
because the login screen and the job-application page must say whose system
this is before anyone signs in, and disclosing nothing beyond what those
pages show.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BRANDING = "/api/v1/org/branding/"


def test_branding_is_public_and_serves_the_configured_name(db):
    from apps.organization.models import OrgSettings

    OrgSettings.objects.create(name="Acme Traders", legal_name="Acme Traders Pvt Ltd")

    response = APIClient().get(BRANDING)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Acme Traders"
    assert body["legal_name"] == "Acme Traders Pvt Ltd"


def test_branding_falls_back_neutrally_before_setup(db):
    """A fresh install with no settings row still renders as 'HRMS'."""
    response = APIClient().get(BRANDING)

    assert response.status_code == 200
    assert response.json() == {"name": "HRMS", "legal_name": "", "logo": None}


def test_branding_exposes_nothing_else(db):
    """Registrations, codes and counters stay behind ORG_SETTINGS/VIEW."""
    from apps.organization.models import OrgSettings

    OrgSettings.objects.create(name="Acme", gstin="22AAAAA0000A1Z5", pan="AAAAA0000A")

    body = APIClient().get(BRANDING).json()

    assert set(body) == {"name", "legal_name", "logo"}
