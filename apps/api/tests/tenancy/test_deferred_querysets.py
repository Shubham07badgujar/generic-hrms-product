"""
A class-level queryset is built when it is used, not when it is imported.

`queryset = Asset.objects.select_related("category")` in a class body runs once,
at import, with no request and therefore no organization. DRF then clones it per
request with `.all()`, and a clone never goes back through the manager. So a
class-level queryset sits outside manager-level tenancy entirely: the predicate
runs at import with nothing bound, or it never runs.

While the manager did not filter, that was invisible. The first app flipped to
strict turned it into an import-time crash that took down the URLconf, the test
collector and `manage.py check` with it -- which is how the hole was found.

`deferred(Model)` records the chain and replays it through the model's default
manager at the moment of use. These tests pin the three shapes the codebase
relies on: read off the class, read off a view instance, and handed to a DRF
relation field.
"""

from __future__ import annotations

import copy

import pytest
from django.db.models import Manager, QuerySet

from core.middleware import acting_as
from core.models import OrgContextMissing
from core.querysets import RECORDED, deferred

pytestmark = pytest.mark.django_db


def _model():
    from apps.notifications.models import Notification

    return Notification


# ------------------------------------------------------------ the three shapes


def test_class_access_evaluates_nothing_and_still_names_its_model():
    """
    What the router and `core.access.routewalk.model_of` read.

    Both look at `ViewSet.queryset.model` on the CLASS, with no request in
    sight. If that access built a queryset, a strict app would raise during URL
    loading -- which is exactly the crash this replaces.
    """
    model = _model()

    class View:
        queryset = deferred(model).select_related("recipient")

    assert View.queryset.model is model
    assert View.queryset.model._meta.object_name.lower() == "notification"


def test_instance_access_builds_a_real_queryset_through_the_manager(org_a):
    """
    What `GenericAPIView.get_queryset` and every `self.queryset` override get.

    A real QuerySet, not the recorder: pagination slices what it is handed and
    a Manager cannot be sliced, so handing one back turned every paginated list
    endpoint into a 500.
    """
    model = _model()

    class View:
        queryset = deferred(model).select_related("recipient")

    with acting_as(org_a.admin, organization=org_a.organization):
        built = View().queryset

    assert isinstance(built, QuerySet)
    assert built.model is model


def test_instance_access_yields_the_recorder_when_the_manager_refuses():
    """
    The metadata case, and why it is not a hole.

    drf-spectacular reads `view.queryset` off an INSTANCE while generating the
    schema: no request, so no organization, so a strict app refuses. Raising
    there made `/api/schema/` a 500 as soon as a second app went strict. What
    comes back answers `.model` and holds no rows, and asking it for rows goes
    through the manager again and is refused again.
    """
    model = _model()

    class View:
        queryset = deferred(model).select_related("recipient")

    with acting_as(None, organization=None):
        fallback = View().queryset

        assert fallback is View.queryset
        assert fallback.model is model
        with pytest.raises(OrgContextMissing):
            fallback.all()


def test_none_answers_without_asking_the_manager():
    """
    An empty queryset needs no tenant, and must not demand one.

    `apply_org_predicate` returns `qs.none()` when nothing is bound, so routing
    that through a strict manager would raise on the very path written to
    handle the unbound case -- which is what made schema generation 500.
    """
    model = _model()

    with acting_as(None, organization=None):
        empty = deferred(model).filter(is_read=False).none()

    assert isinstance(empty, QuerySet)
    assert list(empty) == []


def test_it_is_a_manager_so_drf_relation_fields_accept_it():
    """
    `RelatedField.get_queryset` calls `.all()` on a QuerySet or a Manager.

    Stored in a field's instance dict, no descriptor runs, so it stays a
    Manager -- and `.all()` must hand back something iterable, not another
    recorder.
    """
    from rest_framework import serializers

    model = _model()
    lazy = deferred(model)

    assert isinstance(lazy, Manager)
    field = serializers.PrimaryKeyRelatedField(queryset=lazy, read_only=False)
    assert field.queryset is lazy


# ------------------------------------------------------- recording and replay


def test_the_recorded_chain_is_replayed_at_use(org_a, org_b):
    """
    The filter is applied when the rows are fetched, with the tenant bound then.

    One recorder, read twice under two organizations, must answer differently.
    That is the whole difference from a queryset built once at import.
    """
    model = _model()
    lazy = deferred(model).filter(kind="payroll_processed")

    with acting_as(org_a.admin, organization=org_a.organization):
        mine = model.objects.create(
            recipient=org_a.admin, kind="payroll_processed", title="A"
        )
    with acting_as(org_b.admin, organization=org_b.organization):
        theirs = model.objects.create(
            recipient=org_b.admin, kind="payroll_processed", title="B"
        )

    with acting_as(org_a.admin, organization=org_a.organization):
        seen_by_a = set(lazy.all().values_list("pk", flat=True))
    with acting_as(org_b.admin, organization=org_b.organization):
        seen_by_b = set(lazy.all().values_list("pk", flat=True))

    assert seen_by_a == {mine.pk}
    assert seen_by_b == {theirs.pk}


def test_recording_does_not_touch_the_database(org_a):
    """
    Building the chain must not query, or importing a module would.

    `django_assert_num_queries` is not used here deliberately: the point is that
    NOTHING happens, which a query count of zero on an unbound context cannot
    distinguish from a query that failed.
    """
    model = _model()

    with acting_as(None, organization=None):
        lazy = deferred(model).select_related("recipient").filter(is_read=False)
        assert lazy._steps  # the chain was recorded

        # Only now, at use, does the strict manager get asked -- and refuses.
        with pytest.raises(OrgContextMissing):
            lazy.all()


def test_an_unrecorded_method_evaluates_rather_than_recording(org_a):
    """
    The allowlist is short on purpose.

    Anything not in it falls through to the ordinary Manager proxies and
    evaluates, which is right at use time and loud at import time -- the guard
    test for import-time evaluation catches the latter.
    """
    model = _model()
    assert "count" not in RECORDED

    with acting_as(org_a.admin, organization=org_a.organization):
        assert deferred(model).count() == model.objects.count()


def test_deepcopy_survives_because_drf_copies_declared_fields(org_a):
    """
    DRF deep-copies every declared field per serializer instance.

    A recorder that could not be copied would break every serializer that
    declares one, at the first request rather than at import.
    """
    model = _model()
    lazy = deferred(model).filter(kind="payroll_processed")

    clone = copy.deepcopy(lazy)

    assert clone.model is model
    assert clone._steps == lazy._steps
    with acting_as(org_a.admin, organization=org_a.organization):
        assert isinstance(clone.all(), QuerySet)
