"""
BI: metric computation and — mostly — metric SCOPING.

The arithmetic matters, but the scoping is what this file exists for. A metric
that returns the right number to the wrong person is a disclosure bug wearing a
dashboard, and the whole design rests on one claim: every metric is scope-aware
because it physically cannot reach an unscoped queryset.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.reporting import services
from apps.reporting.registry import MetricError, all_specs, get, visible_to
from core.access import Action, Resource
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------- the registry


def test_every_metric_declares_a_resource_that_exists():
    """
    A metric whose resource is not in the catalogue could never be authorised.

    It would be invisible to everyone and impossible to debug, because the
    failure looks exactly like a permission problem.
    """
    valid = set(Resource.values)
    for spec in all_specs():
        assert spec.resource in valid, f"{spec.key} declares unknown resource {spec.resource}"


def test_no_metric_contains_role_branching():
    """
    The architectural rule, enforced.

    Metrics must differ by SCOPE, never by role. A role check inside a metric
    is the point at which the permission matrix stops being the single source
    of truth about who sees what.
    """
    import inspect

    from apps.reporting import metrics as metrics_module

    source = inspect.getsource(metrics_module)
    for role in ("ceo", "hr_head", "medical_director", "admin", "therapist"):
        assert f'"{role}"' not in source and f"'{role}'" not in source, (
            f"metrics.py references the role '{role}'. Metrics must branch on scope, "
            f"never on role — change the permission matrix instead."
        )


# ----------------------------------------------------------- the catalogue


def test_the_catalogue_is_filtered_by_permission(people, ceo_user):
    """CEO sees every metric; a therapist sees only those they hold the resource for."""
    ceo_keys = {spec.key for spec in visible_to(ceo_user)}
    therapist_keys = {spec.key for spec in visible_to(people["therapist"].user)}

    assert "finance.payroll_cost" in ceo_keys
    assert therapist_keys < ceo_keys
    # An employee holds no JOB_OPENING or APPLICATION grant, so recruitment
    # metrics are absent entirely rather than present and empty.
    assert not any(key.startswith("recruitment.") for key in therapist_keys)


def test_a_metric_the_caller_cannot_see_is_refused_not_merely_hidden(people):
    """Hiding it in the list and serving it on request would be theatre."""
    therapist = people["therapist"].user

    with pytest.raises(AccessDenied):
        services.compute("recruitment.pipeline", user=therapist)


# --------------------------------------------------------------- scoping


def test_headcount_narrows_with_scope(people, ceo_user, admin_user):
    """
    The same function, the same code path, four different answers.

    This is the whole design in one assertion: nobody wrote a per-role
    headcount, and the numbers differ anyway.
    """
    org_wide = services.compute("hr.headcount", user=ceo_user).points[0].value
    admin_view = services.compute("hr.headcount", user=admin_user).points[0].value
    medical = services.compute(
        "hr.headcount", user=people["medical_director"].user
    ).points[0].value
    operations = services.compute(
        "hr.headcount", user=people["operational_head"].user
    ).points[0].value
    team = services.compute("hr.headcount", user=people["senior_doctor"].user).points[0].value
    own = services.compute("hr.headcount", user=people["therapist"].user).points[0].value

    assert org_wide == admin_view == 10
    assert medical == 4
    assert operations == 4
    assert team == 3          # the manager plus their two reports
    assert own == 1           # SELF scope resolves to exactly one row
    assert medical + operations < org_wide


def test_one_department_cannot_see_another_departments_people(people):
    """The isolation requirement, asserted directly."""
    medical = services.compute(
        "hr.headcount", user=people["medical_director"].user, group_by="department"
    )
    operations = services.compute(
        "hr.headcount", user=people["operational_head"].user, group_by="department"
    )

    medical_labels = {point.label for point in medical.points}
    operations_labels = {point.label for point in operations.points}

    assert medical_labels == {"Medical Department"}
    assert operations_labels == {"Operations Department"}
    assert medical_labels.isdisjoint(operations_labels)


def test_a_manager_sees_their_reporting_tree_and_not_their_peers(people):
    """TEAM scope is the reporting tree, not the department."""
    result = services.compute("hr.headcount", user=people["senior_doctor"].user)

    # Sanjay plus Tara and Chandni — not Meera above them, not Operations.
    assert result.points[0].value == 3
    assert "reporting tree" in result.scope_label.lower()


def test_an_employee_sees_only_themselves(people):
    result = services.compute("hr.headcount", user=people["office_boy"].user)

    assert result.points[0].value == 1
    assert "own" in result.scope_label.lower()


def test_the_scope_label_describes_what_was_actually_applied(people, ceo_user):
    """
    The UI prints this. A department figure shown without saying so reads as an
    organisation figure, and someone eventually quotes it in a board meeting.
    """
    assert "organization" in services.compute("hr.headcount", user=ceo_user).scope_label.lower()
    assert "department" in services.compute(
        "hr.headcount", user=people["medical_director"].user
    ).scope_label.lower()


# ------------------------------------------------------------ computation


def test_joiners_and_leavers_respect_the_range(people, ceo_user):
    from apps.employees.models import Employee

    leaver = people["therapist"]
    leaver.date_of_exit = dt.date(2024, 6, 15)
    leaver.save(update_fields=["date_of_exit"])

    inside = services.compute(
        "hr.leavers", user=ceo_user, start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31)
    )
    outside = services.compute(
        "hr.leavers", user=ceo_user, start=dt.date(2025, 1, 1), end=dt.date(2025, 12, 31)
    )

    assert inside.points[0].value == 1
    assert outside.points[0].value == 0


def test_attrition_uses_average_headcount_not_closing(people, ceo_user):
    """
    A team that halved would report over 100% attrition against closing
    headcount — arithmetically defensible and useless to read.
    """
    leaver = people["cre"]
    leaver.date_of_exit = dt.date(2024, 6, 15)
    leaver.save(update_fields=["date_of_exit"])

    result = services.compute(
        "hr.attrition_rate", user=ceo_user, start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31)
    )
    point = result.points[0]

    assert point.value >= 0
    assert point.context["leavers"] == 1
    assert point.context["average_headcount"] > 0


def test_grouping_is_refused_when_the_metric_does_not_offer_it(ceo_user):
    with pytest.raises(MetricError):
        services.compute("hr.attrition_rate", user=ceo_user, group_by="department")


def test_an_absurd_range_is_refused_rather_than_scanned(ceo_user):
    with pytest.raises(MetricError):
        services.compute(
            "hr.headcount_trend",
            user=ceo_user,
            start=dt.date(1990, 1, 1),
            end=dt.date(2030, 1, 1),
        )


def test_a_backwards_range_is_refused(ceo_user):
    with pytest.raises(MetricError):
        services.compute(
            "hr.joiners", user=ceo_user, start=dt.date(2025, 12, 1), end=dt.date(2025, 1, 1)
        )


def test_compute_many_survives_a_metric_the_caller_cannot_see(people):
    """One forbidden tile must not blank a dashboard."""
    results = services.compute_many(
        ["hr.headcount", "recruitment.pipeline", "finance.payroll_cost"],
        user=people["therapist"].user,
    )

    assert results["hr.headcount"] is not None
    assert results["recruitment.pipeline"] is None


def test_unknown_metric_raises(ceo_user):
    with pytest.raises(MetricError):
        services.compute("hr.does_not_exist", user=ceo_user)


# ------------------------------------------------------------- CEO is read-only


def test_ceo_holds_view_and_export_but_no_write_on_reporting(ceo_user):
    from core.access import can

    assert can(ceo_user, Resource.REPORT, Action.VIEW)
    assert can(ceo_user, Resource.REPORT, Action.EXPORT)
    for action in (Action.CREATE, Action.EDIT, Action.DELETE, Action.APPROVE):
        assert not can(ceo_user, Resource.REPORT, action), (
            f"CEO must hold no write action on reporting; found {action}."
        )
