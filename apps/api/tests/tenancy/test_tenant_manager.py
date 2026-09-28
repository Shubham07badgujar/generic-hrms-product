"""
The manager-level tenant predicate actually runs, for every app that claims it.

`TenantManager` existed for a full stage wired to no model at all, and nothing
failed: the view layer kept every HTTP test honest while every service, Celery
task and management command read every customer's rows. A suite that passes
proves nothing about a switch unless something asserts the switch DOES
something. That is this file.

Parametrised over EVERY organization-owned model the product declares, derived
from the app registry. Filtering is unconditional now -- the `STRICT_TENANT_APPS`
set this file once read is gone -- so a model added next year is covered the day
it is written, with no line to remember here and none to add there.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps

from core.middleware import acting_as
from core.models import OrgContextMissing, OrgOwnedModel, OrgOwnedTimestampedModel

pytestmark = pytest.mark.django_db


def _org_owned_models():
    return [
        model
        for model in django_apps.get_models()
        if issubclass(model, OrgOwnedModel | OrgOwnedTimestampedModel)
    ]


#: Resolved once, with the ids as plain strings. An `ids=` callable crashed
#: collection when this list was empty -- pytest hands it a placeholder, not a
#: model -- so the file read as broken rather than giving the clean "nothing is
#: covered" failure `test_every_organization_owned_model_is_covered` exists for.
STRICT_MODELS = _org_owned_models()
STRICT_MODEL_IDS = [m._meta.label for m in STRICT_MODELS]


# ------------------------------------------------- the build enforces it


def test_no_organization_owned_model_escapes_the_filter():
    """
    access.E017, over the models the product actually ships.

    The successor to the `access.W001` warning that counted apps still waiting
    on the rollout. The rollout is finished, so the honest state is an ERROR for
    anything organization-owned that does not filter -- there is no longer a
    transitional state for a warning to describe.
    """
    from core.access.checks import check_tenant_managers_filter

    assert check_tenant_managers_filter(None) == []


def test_the_build_error_bites_a_model_whose_manager_was_replaced():
    """
    Guards the check.

    The way filtering can still be lost is `objects = models.Manager()` on a
    model that is otherwise organization-owned: the column is there, the model
    reads as protected, and every query returns every customer's rows. A check
    that only ever reports nothing would say the same thing whether or not it
    still detects that, so it is handed one.
    """
    from types import SimpleNamespace

    from django.db import models

    from core.access.checks import unfiltered_managers
    from core.models import OrgOwnedManager

    replaced = SimpleNamespace(
        _meta=SimpleNamespace(label="fake.Replaced", default_manager=models.Manager()),
        objects=models.Manager(),
    )
    assert unfiltered_managers(replaced) == ["default manager", "objects"]

    # And the shape that is correct reports nothing, so the assertion above is
    # not satisfied by a function that flags everything.
    scoped = OrgOwnedManager()
    intact = SimpleNamespace(
        _meta=SimpleNamespace(label="fake.Intact", default_manager=scoped),
        objects=scoped,
    )
    assert unfiltered_managers(intact) == []


# ---------------------------------------------------------- per strict model


@pytest.mark.parametrize("model", STRICT_MODELS, ids=STRICT_MODEL_IDS)
def test_a_strict_model_refuses_to_be_read_with_nothing_bound(model):
    """
    Fails closed, per model rather than once.

    Raising rather than returning nothing is the point: an empty queryset in a
    Celery task looks exactly like "no work today".
    """
    with acting_as(None, organization=None):
        with pytest.raises(OrgContextMissing):
            model.objects.all()


@pytest.mark.parametrize("model", STRICT_MODELS, ids=STRICT_MODEL_IDS)
def test_a_strict_model_keeps_its_escape_hatch(model):
    """`all_orgs()` must still work unbound, or platform code has no way out."""
    with acting_as(None, organization=None):
        list(model.objects.all_orgs()[:1])


def test_every_organization_owned_model_is_covered():
    """
    Guards the parametrised tests above.

    With an empty list they collect nothing and pass, which is exactly the
    silent state this file exists to end. The count is asserted against the app
    registry rather than written down, so a new model joins by existing.
    """
    assert STRICT_MODELS, "no organization-owned model was found, so nothing above ran"
    assert len(STRICT_MODELS) == len(_org_owned_models())
    assert len({m._meta.app_label for m in STRICT_MODELS}) >= 15


# ------------------------------------------------ filtering, on real rows


def test_a_strict_app_returns_only_the_bound_organizations_rows(org_a, org_b):
    """
    Notifications, the first app flipped, with rows in both organizations.

    Written without `organization=` anywhere in the query, because that is how
    every service in the codebase writes it -- and that is the query the
    manager has to make safe.
    """
    from apps.notifications.models import Notification

    with acting_as(org_a.admin, organization=org_a.organization):
        mine = Notification.objects.create(
            recipient=org_a.admin, kind="payroll_processed", title="A's run"
        )
    with acting_as(org_b.admin, organization=org_b.organization):
        theirs = Notification.objects.create(
            recipient=org_b.admin, kind="payroll_processed", title="B's run"
        )

    with acting_as(org_a.admin, organization=org_a.organization):
        seen = set(Notification.objects.values_list("pk", flat=True))

    assert mine.pk in seen
    assert theirs.pk not in seen

    # Positive control: both rows really exist, so the absence above is the
    # predicate and not a failed write.
    from .conftest import across_organizations

    with across_organizations():
        everything = set(Notification.objects.all_orgs().values_list("pk", flat=True))
    assert {mine.pk, theirs.pk} <= everything


def test_a_reverse_accessor_needs_no_ambient_organization(org_a):
    """
    `parent.children` is already decided by the parent, so it must not demand one.

    Django builds a reverse related manager from the model's default manager,
    so this arrives at the tenant predicate like any other query. Refusing it
    broke sign-in: the login response reports whether onboarding is pending,
    and computes it before any tenant is bound.
    """
    from apps.onboarding.models import EmployeeOnboarding

    with acting_as(org_a.admin, organization=org_a.organization):
        onboarding = (
            EmployeeOnboarding.objects.filter(employee=org_a.worker_employee).first()
        )
    assert onboarding is not None, "the fixture has no onboarding to read"

    with acting_as(None, organization=None):
        items = list(onboarding.items.all())

    assert all(item.organization_id == org_a.organization.pk for item in items)


def test_a_reverse_accessor_is_still_confined_to_its_parents_organization(
    org_a, org_b
):
    """
    The narrowing half: it uses the PARENT's organization, not whatever is bound.

    Reading A's children while B is bound never returns B's rows. What it
    does return changed in release 2, and got STRICTER: the manager would hand
    back A's rows, because the parent fixes the tenant; the database now shows
    a connection bound to B nothing of A's at all. Both refuse to leak; the
    database refuses harder, and a legitimate caller reading A's children is
    bound to A while doing it.
    """
    from apps.onboarding.models import EmployeeOnboarding

    with acting_as(org_a.admin, organization=org_a.organization):
        onboarding = (
            EmployeeOnboarding.objects.filter(employee=org_a.worker_employee).first()
        )
        expected = {item.pk for item in onboarding.items.all()}

    with acting_as(org_b.admin, organization=org_b.organization):
        seen = {item.pk for item in onboarding.items.all()}

    assert expected, "A's onboarding must have items, or this proves nothing"
    assert not (seen & expected) and not seen, (
        "bound to B, none of A's items are visible -- and certainly none of B's"
    )

    # Bound to A, the same accessor returns exactly A's items.
    with acting_as(org_a.admin, organization=org_a.organization):
        assert {item.pk for item in onboarding.items.all()} == expected



def test_a_strict_app_cannot_fetch_another_organizations_row_by_id(org_a, org_b):
    """
    The object-id case: the one a Celery task handed the wrong tenant's id hits.

    `DoesNotExist`, not a row. That is the failure 5d's negative test needs
    from every app, and it is what "strict" means.
    """
    from apps.notifications.models import Notification

    with acting_as(org_b.admin, organization=org_b.organization):
        theirs = Notification.objects.create(
            recipient=org_b.admin, kind="payroll_processed", title="B's run"
        )

    with acting_as(org_a.admin, organization=org_a.organization):
        with pytest.raises(Notification.DoesNotExist):
            Notification.objects.get(pk=theirs.pk)


@pytest.mark.unbound_organization
def test_the_audit_re_read_imposes_no_tenant_question_of_its_own(org_a):
    """
    The audit trail re-reads a row before an update so it can record the diff.

    It re-read it through the tenant manager. With nothing bound that raised,
    failing a save of a row that carries its own organization; with another
    organization bound it found nothing, and the update was silently never
    audited. The re-read is of the same row, so it asks no tenant question.

    Renamed in release 2: the case is no longer "nothing bound", because the
    database now refuses an update that has no organization in force -- the
    fail-closed behaviour the whole design wants. The property under test is
    the same one, and the row's own organization is what satisfies it.
    """
    from apps.audit.models import AuditAction, AuditLog

    # Bound, because from release 2 the DATABASE is the second answer to the
    # same question: an update with nothing bound matches no rows at all, which
    # is the fail-closed behaviour this design wants. What the test is about is
    # unchanged -- the audit re-read must not impose a tenant question of its
    # own on a row that already carries its organization.
    employee = org_a.worker_employee
    with acting_as(None, organization=org_a.organization):
        employee.first_name = "Renamed"
        employee.save(update_fields=["first_name"])

    with acting_as(None, organization=org_a.organization):
        entry = AuditLog.objects.filter(
            entity_type="employees.Employee",
            entity_id=str(employee.pk),
            action=AuditAction.UPDATE,
        ).first()
    assert entry is not None, "the update wrote no audit row"
    assert entry.after.get("first_name") == "Renamed"
