"""
Two organizations, borrowed from the tenancy suite.

Re-exported rather than rebuilt. The question this package asks -- can the
platform operator reach a customer's data, and can a customer reach the
platform -- needs exactly the world `tests/tenancy/conftest.py` already
builds, and a second copy of it would be a second thing to keep in step with
the models.
"""

from __future__ import annotations

from tests.tenancy.conftest import api_for, org_a, org_b  # noqa: F401
