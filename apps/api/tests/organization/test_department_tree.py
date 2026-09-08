"""
The department tree's cycle guard.

`Department.clean()` refuses a parent chain that loops. Department scope
resolves to a department PLUS all its descendants, so a cycle is not a tidiness
problem: `descendant_ids()` walks that chain to decide what a department head
can see, and a loop there is an unbounded walk in the middle of an
authorization decision.

Written because the guard had no test at all -- discovered when an import it
depends on was accidentally removed during the multi-tenancy work and the
entire suite stayed green. A linter caught it. That is too thin a margin for a
control that authorization depends on.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.organization.models import Department
from core.access.catalog import DepartmentKind

pytestmark = pytest.mark.django_db


def _dept(code: str, parent: Department | None = None) -> Department:
    return Department.objects.create(
        name=code.title(), code=code, kind=DepartmentKind.OTHER, parent_department=parent
    )


def test_a_normal_hierarchy_is_accepted():
    root = _dept("ROOT")
    child = _dept("CHILD", parent=root)

    child.full_clean()  # must not raise

    assert child.parent_department == root


def test_a_department_cannot_be_its_own_parent():
    dept = _dept("SOLO")
    dept.parent_department = dept

    with pytest.raises(ValidationError) as excinfo:
        dept.clean()

    assert "parent_department" in excinfo.value.message_dict


def test_a_two_step_cycle_is_refused():
    root = _dept("ROOT")
    child = _dept("CHILD", parent=root)

    # Closing the loop: root -> child -> root.
    root.parent_department = child

    with pytest.raises(ValidationError):
        root.clean()


def test_a_longer_cycle_is_refused():
    """The walk must terminate however far around the loop the entry point is."""
    a = _dept("A")
    b = _dept("B", parent=a)
    c = _dept("C", parent=b)

    a.parent_department = c

    with pytest.raises(ValidationError):
        a.clean()


def test_descendants_are_reachable_from_the_root():
    root = _dept("ROOT")
    child = _dept("CHILD", parent=root)
    grandchild = _dept("GRAND", parent=child)

    assert {child.pk, grandchild.pk} <= set(root.descendant_ids())
