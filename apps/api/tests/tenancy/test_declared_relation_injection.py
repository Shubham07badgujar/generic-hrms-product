"""
A relation id in a request body cannot reach another organization's row.

`test_write_injection.py` covered the relations DRF generates on a
ModelSerializer. This covers the ones written out by hand -- declared fields,
and every relation on a plain `Serializer` -- which nothing checked at all.

A probe found 23 of them across 16 serializers, and every one resolved another
organization's id. Two were exploitable end to end over HTTP:

* `POST /api/v1/exits/` let one company's admin open an exit for another
  company's employee. The victim's status was set to resigned, their login and
  company mailbox were flagged, and a final settlement was created -- with every
  audit row stamped to the ATTACKER's organization, so the victim's own audit
  trail showed nothing.
* `POST /api/v1/resignations/` filed a resignation on the other employee's
  behalf.

The matrix below is DERIVED, not listed: it imports every module under `apps/`,
finds every writable relation field declared on any serializer, and tries each
with the other organization's row and then with its own. A field added next
year is covered the day it is added. `test_the_matrix_found_the_fields` guards
against the discovery silently finding nothing.
"""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import pathlib

import pytest
from rest_framework import serializers as drf
from rest_framework.relations import ManyRelatedField, RelatedField

from core.access.routewalk import rows_for
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

#: The count the first probe found. The discovery must never report fewer
#: without someone deciding a field was removed on purpose.
KNOWN_DECLARED_FIELDS = 23


def _declared_relation_fields():
    """(dotted serializer path, field name, serializer class, field) for every one."""
    root = pathlib.Path(__file__).resolve().parents[2]
    found = []
    for path in sorted((root / "apps").rglob("*.py")):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        module_name = ".".join(path.relative_to(root).with_suffix("").parts)
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if not (
                inspect.isclass(obj)
                and issubclass(obj, drf.Serializer)
                and obj.__module__ == module_name
            ):
                continue
            for field_name, field in getattr(obj, "_declared_fields", {}).items():
                relation = (
                    field.child_relation if isinstance(field, ManyRelatedField) else field
                )
                if (
                    isinstance(relation, RelatedField)
                    and not field.read_only
                    and getattr(relation, "queryset", None) is not None
                ):
                    found.append((f"{module_name}.{name}", field_name, obj, field))
    return found


DECLARED = _declared_relation_fields()


def _model_of(field):
    relation = field.child_relation if isinstance(field, ManyRelatedField) else field
    return relation.queryset.model


def _a_row(model, organization):
    from .conftest import across_organizations

    # Fetching a row belonging to the OTHER organization, to offer it to a
    # serializer bound to this one: the fixture's own reach, not the product's.
    with across_organizations():
        return rows_for(model, organization.pk).filter(is_active=True).first()


def _payload(field, row):
    return [str(row.pk)] if isinstance(field, ManyRelatedField) else str(row.pk)


def _errors_for(serializer_class, field_name, field, row, world):
    with acting_as(world.admin, organization=world.organization):
        serializer = serializer_class(data={field_name: _payload(field, row)})
        serializer.is_valid()
        return serializer.errors.get(field_name)


def test_the_matrix_found_the_fields():
    """Guards the guard: an empty discovery would make every case vacuous."""
    assert len(DECLARED) >= KNOWN_DECLARED_FIELDS, (
        f"found {len(DECLARED)} declared relation fields; the probe found "
        f"{KNOWN_DECLARED_FIELDS}. Either discovery broke or fields were removed."
    )


@pytest.mark.parametrize(
    ("path", "field_name", "serializer_class", "field"),
    DECLARED,
    ids=[f"{p}.{f}" for p, f, _, _ in DECLARED],
)
def test_a_declared_relation_refuses_another_organizations_row(
    path, field_name, serializer_class, field, org_a, org_b
):
    model = _model_of(field)
    theirs = _a_row(model, org_b.organization)
    mine = _a_row(model, org_a.organization)
    assert theirs is not None and mine is not None, (
        f"{path}.{field_name}: the fixture has no {model.__name__} row in both "
        f"organizations, so this case would prove nothing"
    )

    refused = _errors_for(serializer_class, field_name, field, theirs, org_a)
    assert refused, (
        f"{path}.{field_name} accepted {org_b.slug}'s {model.__name__} while "
        f"acting for {org_a.slug}"
    )

    # Positive control: the same field accepts the caller's own row, so the
    # refusal above is the tenant scope and not a lookup that rejects everything.
    accepted = _errors_for(serializer_class, field_name, field, mine, org_a)
    assert not accepted, (
        f"{path}.{field_name} refused {org_a.slug}'s own {model.__name__}: {accepted}"
    )


# ----------------------------------------------- the two exploited endpoints


def _exit_payload(employee):
    from apps.offboarding.models import ExitWorkflow

    return {
        "employee": str(employee.pk),
        "exit_type": ExitWorkflow._meta.get_field("exit_type").choices[0][0],
        "last_working_date": str(dt.date.today() + dt.timedelta(days=30)),
    }


def _resignation_payload(employee):
    return {
        "employee": str(employee.pk),
        "requested_last_working_date": str(dt.date.today() + dt.timedelta(days=30)),
        "reason": "Moving on",
    }


def _employee_state(employee):
    from apps.employees.models import Employee

    from .conftest import across_organizations

    # Reads the OTHER organization's row, to prove the request left it alone.
    with across_organizations():
        row = Employee.objects.all_orgs().get(pk=employee.pk)
    return (row.status, row.is_active, row.updated_at)


def test_one_organization_cannot_offboard_anothers_employee(org_a, org_b, api_for):
    from apps.offboarding.models import ExitWorkflow

    victim = org_b.worker_employee
    before = _employee_state(victim)

    response = api_for(org_a.admin).post(
        "/api/v1/exits/", _exit_payload(victim), format="json"
    )

    assert response.status_code == 400, (
        f"expected refusal, got {response.status_code}: {response.content[:300]}"
    )
    assert "employee" in response.content.decode().lower()
    from .conftest import across_organizations

    with across_organizations():
        assert not ExitWorkflow.objects.all_orgs().filter(employee=victim).exists()
    assert _employee_state(victim) == before, "the other organization's employee changed"


def test_one_organization_cannot_file_a_resignation_for_anothers_employee(
    org_a, org_b, api_for
):
    from apps.offboarding.models import ResignationRequest

    victim = org_b.worker_employee
    existing = set(
        ResignationRequest.objects.all_orgs()
        .filter(employee=victim)
        .values_list("pk", flat=True)
    )

    response = api_for(org_a.admin).post(
        "/api/v1/resignations/", _resignation_payload(victim), format="json"
    )

    assert response.status_code == 400, (
        f"expected refusal, got {response.status_code}: {response.content[:300]}"
    )
    assert "employee" in response.content.decode().lower()
    created = (
        ResignationRequest.objects.all_orgs()
        .filter(employee=victim)
        .exclude(pk__in=existing)
    )
    assert not created.exists()


def test_the_same_admin_can_still_offboard_their_own_employee(org_a, api_for):
    """Pairs the refusals above with the permission they must leave intact."""
    from apps.offboarding.models import ExitWorkflow

    own = org_a.worker_employee
    response = api_for(org_a.admin).post(
        "/api/v1/exits/", _exit_payload(own), format="json"
    )

    assert response.status_code == 201, (
        f"A's admin cannot offboard A's employee: {response.status_code} "
        f"{response.content[:300]}"
    )
    from .conftest import across_organizations

    with across_organizations():
        workflow = ExitWorkflow.objects.all_orgs().get(employee=own)
    assert workflow.organization_id == org_a.organization.pk


def test_the_same_admin_can_still_file_a_resignation_for_their_own_employee(
    org_a, api_for
):
    response = api_for(org_a.admin).post(
        "/api/v1/resignations/", _resignation_payload(org_a.worker_employee),
        format="json",
    )

    assert response.status_code == 201, (
        f"A's admin cannot file A's resignation: {response.status_code} "
        f"{response.content[:300]}"
    )


# -------------------------------------------------------- the build gate


def test_the_check_flags_a_plain_serializer_with_an_unscoped_declared_relation():
    """
    access.E010 must catch exactly the shape that was exploited.

    Before this change it asked only ModelSerializers, and only about generated
    fields, so all three of these read as fine.
    """
    from apps.employees.models import Employee
    from core.access.checks import serializer_lacks_relation_scoping
    from core.api.serializers import ScopedRelationsMixin
    from core.querysets import deferred

    # `deferred`, as production serializers declare it. `Employee.objects.all()`
    # in a class body is a query built at definition time, which raises once
    # employees filter at the manager -- and the check under test looks at
    # the serializer's scoping, not at how its queryset was spelled.
    class Unscoped(drf.Serializer):
        employee = drf.PrimaryKeyRelatedField(queryset=deferred(Employee))

    class UnscopedMany(drf.Serializer):
        employees = drf.PrimaryKeyRelatedField(
            queryset=deferred(Employee), many=True
        )

    class Scoped(ScopedRelationsMixin, drf.Serializer):
        employee = drf.PrimaryKeyRelatedField(queryset=deferred(Employee))

    class ReadOnly(drf.Serializer):
        employee = drf.PrimaryKeyRelatedField(read_only=True)

    assert serializer_lacks_relation_scoping(Unscoped)
    assert serializer_lacks_relation_scoping(UnscopedMany)
    assert not serializer_lacks_relation_scoping(Scoped)
    assert not serializer_lacks_relation_scoping(ReadOnly)


def test_no_serializer_in_the_project_lacks_relation_scoping():
    """Every serializer found by discovery carries the scope."""
    from core.access.checks import serializer_lacks_relation_scoping

    unscoped = sorted({p for p, _, cls, _ in DECLARED if serializer_lacks_relation_scoping(cls)})
    assert not unscoped, f"serializers resolving ids across organizations: {unscoped}"
