"""
The manager-level tenant predicate actually runs, for every app that claims it.

`TenantManager` existed for a full stage wired to no model at all, and nothing
failed: the view layer kept every HTTP test honest while every service, Celery
task and management command read every customer's rows. A suite that passes
proves nothing about a switch unless something asserts the switch DOES
something. That is this file.

Parametrised over `STRICT_TENANT_APPS`, so each app flipped in the rollout is
covered the moment it is added to the set -- no line to remember here.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps

from core.access.tenancy import STRICT_TENANT_APPS
from core.middleware import acting_as
from core.models import OrgContextMissing, OrgOwnedModel, OrgOwnedTimestampedModel

pytestmark = pytest.mark.django_db


def _org_owned_models():
    return [
        model
        for model in django_apps.get_models()
        if issubclass(model, OrgOwnedModel | OrgOwnedTimestampedModel)
    ]


def _strict_models():
    return [m for m in _org_owned_models() if m._meta.app_label in STRICT_TENANT_APPS]


#: Resolved once, with the ids as plain strings. An `ids=` callable crashed
#: collection when the set was empty -- pytest hands it a placeholder, not a
#: model -- so an unfinished rollout read as a broken test file rather than as
#: the clean "nothing is strict" failure `test_the_strict_set_is_not_empty`
#: exists to give.
STRICT_MODELS = _strict_models()
STRICT_MODEL_IDS = [m._meta.label for m in STRICT_MODELS]


# ------------------------------------------------------- the switch is honest


def test_every_strict_app_label_names_an_app_that_owns_tenant_data():
    """
    A typo must not read as a finished rollout.

    `"notification"` for `"notifications"` would leave the real app unfiltered
    while the set, and the rollout warning, both said it was done.
    """
    owning = {m._meta.app_label for m in _org_owned_models()}

    stray = STRICT_TENANT_APPS - owning
    assert not stray, (
        f"STRICT_TENANT_APPS names apps that own no organization-owned models: "
        f"{sorted(stray)}. A misspelt label here filters nothing."
    )


def test_the_rollout_warning_names_exactly_the_apps_not_yet_strict():
    """access.W001 must agree with the set, in both directions."""
    from core.access.checks import check_tenant_manager_rollout

    owning = {m._meta.app_label for m in _org_owned_models()}
    pending = owning - STRICT_TENANT_APPS
    messages = check_tenant_manager_rollout(None)

    if not pending:
        assert messages == []
        return

    (warning,) = messages
    assert warning.id == "access.W001"
    for label in pending:
        assert f"{label} (" in warning.msg
    for label in STRICT_TENANT_APPS:
        assert f"{label} (" not in warning.msg


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


def test_the_strict_set_is_not_empty():
    """
    Guards the parametrised tests above.

    With an empty set they collect nothing and pass, which is exactly the
    silent state this file exists to end.
    """
    assert STRICT_MODELS, "no app is strict yet, so nothing above ran"


# ------------------------------------------------ filtering, on real rows


def test_a_strict_app_returns_only_the_bound_organizations_rows(org_a, org_b):
    """
    Notifications, the first app flipped, with rows in both organizations.

    Written without `organization=` anywhere in the query, because that is how
    every service in the codebase writes it -- and that is the query the
    manager has to make safe.
    """
    from apps.notifications.models import Notification

    assert "notifications" in STRICT_TENANT_APPS

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
    everything = set(Notification.objects.all_orgs().values_list("pk", flat=True))
    assert {mine.pk, theirs.pk} <= everything


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
