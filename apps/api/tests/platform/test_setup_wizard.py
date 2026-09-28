"""
The first-time setup wizard.

Two properties carry the whole design, and each has a test whose failure would
be silent otherwise:

  * completion is COMPUTED, so deleting the data that satisfied a step reopens
    it. A stored "completed" flag would report a finished setup for an
    organization that can no longer hire anybody.
  * PENDING_SETUP is a WORKING state. The administrator is doing the setup, so
    an organization that locked them out until setup was finished could never
    be finished at all.
"""

from __future__ import annotations

import pytest

from apps.organization.setup import (
    SETUP_STEPS,
    SetupError,
    finish_setup,
    setup_state,
)
from apps.platform.services.provisioning import provision_organization

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def provisioned():
    return provision_organization(
        name="Northwind Health",
        slug="northwind",
        admin_email="admin@northwind.example",
        admin_first_name="Asha",
    )


def _state(organization):
    organization.refresh_from_db()
    return setup_state(organization)


def _step(state, key):
    return next(s for s in state["steps"] if s["key"] == key)


# ---------------------------------------------------------------------------
# What a freshly provisioned organization looks like
# ---------------------------------------------------------------------------


def test_the_seeded_steps_are_already_complete(provisioned):
    """
    Provisioning seeds roles, leave policies and shift rules, so those steps
    open green. Shown rather than hidden: an administrator who can see which
    three steps are actually theirs to answer starts with those.
    """
    state = _state(provisioned.organization)

    for key in ("roles", "leave_policy", "attendance_policy"):
        assert _step(state, key)["complete"], f"{key} should be seeded complete"


def test_the_steps_only_the_customer_can_answer_are_outstanding(provisioned):
    """
    Departments, locations and designations are not seeded, and should not be:
    a generic default department is a guess about someone's company that they
    then have to find and delete.
    """
    state = _state(provisioned.organization)

    for key in ("departments", "locations", "designations"):
        assert not _step(state, key)["complete"], f"{key} should start empty"
    assert set(state["blocking"]) >= {"departments", "locations", "designations"}
    assert not state["can_finish"]


def test_setup_is_a_working_state_not_a_locked_one(provisioned):
    """
    The administrator has to be able to read and write DURING setup, because
    setup IS reading and writing. An organization locked until setup finished
    could never finish.
    """
    from apps.organization.models import OPERATIONAL_STATUSES, OrgStatus

    state = _state(provisioned.organization)
    assert state["status"] == OrgStatus.PENDING_SETUP
    assert state["in_setup"]
    assert OrgStatus.PENDING_SETUP in OPERATIONAL_STATUSES


# ---------------------------------------------------------------------------
# Finishing
# ---------------------------------------------------------------------------


def _satisfy_outstanding(organization):
    """Do the work the wizard is asking for, through the real tables."""
    from core.access.catalog import DepartmentKind
    from core.middleware import acting_as

    organization.legal_name = "Northwind Health Private Limited"
    organization.save(update_fields=["legal_name", "updated_at"])

    with acting_as(None, organization=organization):
        from apps.organization.models import Department, Designation, Location

        Department.objects.create(name="People", code="HR", kind=DepartmentKind.HR)
        Location.objects.create(name="Head office", code="HO", city="Pune", state="MH")
        Designation.objects.create(title="Officer", department=None)


def test_finishing_is_refused_while_a_required_step_is_outstanding(provisioned):
    """
    And it names the steps. The administrator is looking at ten rows; "setup
    incomplete" tells them nothing they can act on.
    """
    with pytest.raises(SetupError, match="Departments"):
        finish_setup(provisioned.organization)


def test_finishing_makes_the_organization_active(provisioned):
    from apps.organization.models import OrgStatus

    _satisfy_outstanding(provisioned.organization)

    state = _state(provisioned.organization)
    assert state["can_finish"], state["blocking"]

    finish_setup(provisioned.organization, actor=provisioned.admin)
    provisioned.organization.refresh_from_db()
    assert provisioned.organization.status == OrgStatus.ACTIVE


def test_finishing_twice_is_refused(provisioned):
    _satisfy_outstanding(provisioned.organization)
    finish_setup(provisioned.organization)
    with pytest.raises(SetupError, match="nothing to finish"):
        finish_setup(provisioned.organization)


def test_finishing_is_audited(provisioned):
    """"Who put this organization live, and when" has to be answerable."""
    from apps.audit.models import AuditLog

    _satisfy_outstanding(provisioned.organization)
    finish_setup(provisioned.organization, actor=provisioned.admin)

    from core.middleware import acting_as

    with acting_as(None, organization=provisioned.organization):
        entry = (
            AuditLog.objects.filter(
                organization=provisioned.organization,
                entity_type="organization.Organization",
            )
            .order_by("-occurred_at")
            .first()
        )
    assert entry is not None, "finishing setup wrote no audit row"
    assert entry.after.get("event") == "setup_finished"
    assert entry.before.get("status") == "pending_setup"


# ---------------------------------------------------------------------------
# The property that a stored flag would not have
# ---------------------------------------------------------------------------


def test_removing_the_data_reopens_the_step(provisioned):
    """
    The reason completion is computed rather than stored.

    Deactivating the last department genuinely means this organization cannot
    hire anybody, and the wizard says so. A stored "departments: done" flag
    would keep reporting a finished setup, and the failure would surface as a
    confusing error on the hiring screen weeks later.
    """
    from apps.organization.models import Department
    from core.models import org_scoped

    _satisfy_outstanding(provisioned.organization)
    assert _step(_state(provisioned.organization), "departments")["complete"]

    from core.middleware import acting_as

    with acting_as(None, organization=provisioned.organization):
        updated = org_scoped(Department, provisioned.organization).update(is_active=False)
    assert updated, "no department was deactivated, so the step could not reopen"

    reopened = _state(provisioned.organization)
    assert not _step(reopened, "departments")["complete"]
    assert "departments" in reopened["blocking"]


def test_one_organizations_setup_says_nothing_about_anothers(provisioned):
    """
    Every predicate is organization-scoped. Unscoped, the first customer to
    create a department would mark the step complete for every customer on the
    deployment -- and their wizard would tell them to move on.
    """
    other = provision_organization(
        name="Aperture Systems",
        slug="aperture",
        admin_email="admin@aperture.example",
    )

    _satisfy_outstanding(provisioned.organization)

    assert _step(_state(provisioned.organization), "departments")["complete"]
    assert not _step(_state(other.organization), "departments")["complete"]


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def _client_for(email, password):
    from rest_framework.test import APIClient

    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/auth/login/", {"email": email, "password": password}, format="json"
    )
    assert response.status_code == 200, response.content[:200]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client


def test_the_administrator_walks_the_wizard_over_http(provisioned):
    from apps.organization.models import OrgStatus

    provisioned.admin.set_password("setup-password-12345")
    provisioned.admin.must_change_password = False
    provisioned.admin.save(update_fields=["password", "must_change_password"])
    client = _client_for(provisioned.admin.email, "setup-password-12345")

    first = client.get("/api/v1/org/setup/")
    assert first.status_code == 200, first.content[:200]
    assert first.json()["in_setup"]
    assert not first.json()["can_finish"]

    # 422, not 400: the request is well-formed and the caller is entitled to
    # make it. The system will not allow it YET, which is a business rule.
    refused = client.post("/api/v1/org/setup/", {}, format="json")
    assert refused.status_code == 422, refused.status_code

    _satisfy_outstanding(provisioned.organization)

    finished = client.post("/api/v1/org/setup/", {}, format="json")
    assert finished.status_code == 200, finished.content[:200]
    assert finished.json()["status"] == OrgStatus.ACTIVE

    provisioned.organization.refresh_from_db()
    assert provisioned.organization.status == OrgStatus.ACTIVE


def test_the_wizard_is_not_reachable_by_an_ordinary_employee(org_a, api_for):
    """
    Gated on ORG_SETTINGS, the resource that already owns this screen, rather
    than on a new SETUP resource nobody needed in the matrix.
    """
    client = api_for(org_a.worker)
    assert client.get("/api/v1/org/setup/").status_code == 403


def test_every_step_declares_whether_it_blocks_finishing():
    """
    Guards the guard. A step added with no `required` decision, or an advisory
    step whose predicate silently returns True, would make `can_finish` mean
    less than it appears to.
    """
    assert SETUP_STEPS, "the wizard has no steps at all"
    required = [s for s in SETUP_STEPS if s.required]
    assert len(required) >= 5, "almost nothing blocks finishing"

    for step in SETUP_STEPS:
        assert step.key and step.title and step.route
        assert callable(step.satisfied)
        if not step.required:
            assert "Advisory" in step.detail, (
                f"{step.key} does not block finishing, and the wizard should "
                f"say so where the administrator can read it"
            )


# ---------------------------------------------------------------------------
# Where finishing lands: the subscription decides, not the wizard
# ---------------------------------------------------------------------------
#
# `finish_setup` used to write ACTIVE unconditionally, which made it a second
# writer of `Organization.status` with its own opinion. A customer who finished
# setup during their trial then read ACTIVE while their subscription said
# `trialing` -- two lifecycles disagreeing about one customer. It now asks the
# subscriptions service where to land.


@pytest.fixture
def on_a_plan():
    """Provisioned WITH plans, so the organization starts on a trial."""
    from django.core.management import call_command

    call_command("seed_plans", verbosity=0)
    return provision_organization(
        name="Southwind Clinics",
        slug="southwind",
        admin_email="admin@southwind.example",
    )


def test_finishing_during_a_trial_lands_on_trial(on_a_plan):
    from apps.organization.models import OrgStatus
    from apps.platform.models import Subscription

    from tests.conftest import across_organizations

    organization = on_a_plan.organization
    with across_organizations():
        subscription = Subscription.objects.get(
            organization=organization, is_active=True
        )
    assert subscription.status == "trialing", "precondition: provisioning starts a trial"

    _satisfy_outstanding(organization)
    finish_setup(organization, actor=on_a_plan.admin)
    organization.refresh_from_db()

    assert organization.status == OrgStatus.TRIAL, (
        "finishing setup mid-trial must not claim the customer is ACTIVE "
        "while their subscription says trialing"
    )


def test_finishing_on_a_paid_subscription_lands_on_active(on_a_plan):
    """
    The positive control for the test above: the answer really does come from
    the subscription, rather than TRIAL having replaced ACTIVE as a new
    constant.
    """
    from apps.organization.models import OrgStatus
    from apps.platform.services.subscriptions import set_status

    organization = on_a_plan.organization
    # Paid during setup. The organization stays in PENDING_SETUP -- a
    # subscription change must not skip the wizard -- and only finishing
    # moves it.
    set_status(organization, status="active")
    organization.refresh_from_db()
    assert organization.status == OrgStatus.PENDING_SETUP

    _satisfy_outstanding(organization)
    finish_setup(organization)
    organization.refresh_from_db()
    assert organization.status == OrgStatus.ACTIVE


def test_finishing_while_past_due_still_goes_live(on_a_plan):
    """
    PAST_DUE deliberately maps to "leave the organization alone" -- an unpaid
    invoice warns and switches nothing off. For a company leaving setup,
    "alone" means live, not stuck in the wizard.
    """
    from apps.organization.models import OPERATIONAL_STATUSES, OrgStatus
    from apps.platform.services.subscriptions import set_status

    organization = on_a_plan.organization
    set_status(organization, status="past_due")

    _satisfy_outstanding(organization)
    finish_setup(organization)
    organization.refresh_from_db()
    assert organization.status == OrgStatus.ACTIVE
    assert organization.status in OPERATIONAL_STATUSES


def test_the_audit_row_records_where_finishing_actually_landed(on_a_plan):
    """
    "Who put this organization live, and into what" -- the row must name TRIAL
    when that is what happened, not the ACTIVE the old code would have logged.
    """
    from apps.audit.models import AuditLog
    from apps.organization.models import OrgStatus

    organization = on_a_plan.organization
    _satisfy_outstanding(organization)
    finish_setup(organization, actor=on_a_plan.admin)

    from core.middleware import acting_as

    with acting_as(None, organization=organization):
        entry = (
            AuditLog.objects.filter(
                organization=organization, entity_type="organization.Organization"
            )
            .order_by("-occurred_at")
            .first()
        )
    assert entry is not None, "finishing setup wrote no audit row"
    assert entry.after.get("event") == "setup_finished"
    assert entry.after.get("status") == OrgStatus.TRIAL
