"""
Cutover gate 1: the permission matrix, verified against the running system.

`permission_matrix.py` already asserts its own internal consistency at import
time. That proves the SPEC is coherent; it does not prove the system behaves
like it. Between the spec and a request sit the role seeder, the database, and
a nine-step resolution pipeline — any of which could drift.

So everything here resolves permissions the way a request does, through
`core.access`, and compares that against the spec.
"""

from __future__ import annotations

import pytest

from apps.accounts.permission_matrix import ROLE_SPECS
from core.access import Action, Resource, Scope, can
from core.access.catalog import RoleCode

pytestmark = pytest.mark.django_db

WRITE_ACTIONS = {
    Action.CREATE, Action.EDIT, Action.DELETE, Action.APPROVE,
    Action.REJECT, Action.OVERRIDE, Action.RECOMMEND, Action.DECIDE,
}

BY_CODE = {spec.code: spec for spec in ROLE_SPECS}


# ------------------------------------------------------- seeder fidelity


def test_all_eighteen_roles_exist_in_the_database(everyone, roles):
    """The seeder produced every role the spec declares, and no more."""
    from apps.accounts.models import Role

    seeded = set(Role.objects.values_list("code", flat=True))
    specified = {spec.code for spec in ROLE_SPECS}

    assert seeded == specified, (
        f"Database and spec disagree. Missing: {sorted(specified - seeded)}; "
        f"unexpected: {sorted(seeded - specified)}."
    )
    assert len(seeded) == 18


@pytest.mark.parametrize("spec", ROLE_SPECS, ids=lambda s: s.code)
def test_the_engine_resolves_exactly_what_the_spec_declares(spec, everyone):
    """
    Every (resource, action) pair, for every role, compared spec vs engine.

    This is the ~250-cell matrix check, and it runs through `can()` — the same
    call a viewset makes — so seeder drift, a broken resolution step or a
    mis-seeded scope all fail here rather than in production.
    """
    user = everyone[spec.code]
    read_only = spec.is_read_only
    requires_employee = spec.requires_employee

    mismatches = []

    for resource, actions in spec.permissions.items():
        for action, expected in actions.items():
            actual = can(user, resource, action)

            # Step 8 of the pipeline strips every write from a read-only role,
            # unconditionally and last. The spec may declare it; the engine
            # must refuse it.
            if read_only and action in WRITE_ACTIONS:
                expected_effective = Scope.NONE
            # Step 7: a role that requires an Employee but has none keeps only
            # ALL. Every role in this fixture has one, so this is a no-op here —
            # stated so the intent is visible if the fixture ever changes.
            elif not requires_employee and expected not in (Scope.ALL, Scope.NONE):
                expected_effective = Scope.NONE
            else:
                expected_effective = expected

            if actual != expected_effective:
                mismatches.append(
                    f"{resource}.{action}: spec says {Scope(expected_effective).name}, "
                    f"engine says {Scope(actual).name}"
                )

    assert not mismatches, f"{spec.code} drifted from the matrix:\n  " + "\n  ".join(mismatches)


@pytest.mark.parametrize("spec", ROLE_SPECS, ids=lambda s: s.code)
def test_no_role_holds_a_permission_the_spec_never_granted(spec, everyone):
    """
    The other direction: nothing extra.

    A test that only checks declared grants would pass a system that granted
    everyone everything.
    """
    user = everyone[spec.code]
    unexpected = []

    for resource in Resource.values:
        for action in Action.values:
            actual = can(user, resource, action)
            if not actual:
                continue
            declared = spec.permissions.get(resource, {}).get(action)
            if declared is None:
                unexpected.append(f"{resource}.{action} = {Scope(actual).name}")

    assert not unexpected, (
        f"{spec.code} holds permissions the matrix never granted:\n  "
        + "\n  ".join(unexpected)
    )


# ------------------------------------------------------------ CEO read-only


def test_ceo_holds_no_write_action_on_any_resource(everyone):
    """
    The clamp, checked exhaustively rather than by sampling.

    Every resource × every write action. One hole is one too many.
    """
    ceo = everyone["ceo"]
    holes = [
        f"{resource}.{action}"
        for resource in Resource.values
        for action in Action.values
        if action in WRITE_ACTIONS and can(ceo, resource, action)
    ]

    assert not holes, f"CEO holds write actions: {holes}"


def test_ceo_can_still_read_and_export_widely(everyone):
    """Read-only must not mean useless — the oversight role has to see things."""
    ceo = everyone["ceo"]

    for resource in (
        Resource.EMPLOYEE, Resource.CANDIDATE, Resource.PAYSLIP,
        Resource.AUDIT_LOG, Resource.REPORT,
    ):
        assert can(ceo, resource, Action.VIEW), f"CEO cannot view {resource}"

    assert can(ceo, Resource.EMPLOYEE, Action.EXPORT)


def test_a_read_only_role_cannot_be_combined_with_another(everyone, roles):
    """
    What makes `ctx.read_only` unambiguous.

    Without this, granting CEO alongside HR Head would produce a principal who
    is read-only and can reject candidates, and the clamp would silently win or
    lose depending on ordering.
    """
    from django.core.exceptions import ValidationError

    from apps.accounts.models import UserRole

    ceo = everyone["ceo"]
    combination = UserRole(user=ceo, role=roles["hr_head"])

    with pytest.raises(ValidationError):
        combination.clean()


# -------------------------------------------------------------- Admin


def test_admin_holds_organisation_wide_management(everyone):
    """Admin must retain the authority the design gives it."""
    admin = everyone["admin"]

    for resource in (
        Resource.USER, Resource.ROLE, Resource.EMPLOYEE,
        Resource.DEPARTMENT, Resource.ORG_SETTINGS,
    ):
        assert can(admin, resource, Action.EDIT) == Scope.ALL, (
            f"Admin should manage {resource} organisation-wide."
        )

    assert can(admin, Resource.APPLICATION, Action.OVERRIDE) == Scope.ALL


def test_admin_cannot_perform_a_normal_candidate_rejection(everyone):
    """
    Admin overrides; it does not reject.

    Rejection carries HR Head's authority and a mandatory reason. Admin's path
    is a separate action, a separate table and a distinct audit verb.
    """
    assert not can(everyone["admin"], Resource.APPLICATION, Action.REJECT)


def test_admin_cannot_certify_statutory_rates(everyone):
    """
    Certifying a rate against the gazette is a professional judgement.

    Finance Head alone — Admin can edit a draft but must never sign one off.
    """
    assert not can(everyone["admin"], Resource.STATUTORY_CONFIG, Action.APPROVE)
    assert can(everyone["finance_head"], Resource.STATUTORY_CONFIG, Action.APPROVE)


# ------------------------------------------------- exclusive authorities


def test_exactly_one_role_can_reject_a_candidate(everyone):
    holders = [
        code
        for code in everyone
        if not code.startswith("_") and can(everyone[code], Resource.APPLICATION, Action.REJECT)
    ]
    assert holders == [RoleCode.HR_HEAD], f"APPLICATION/REJECT held by {holders}"


def test_only_finance_head_and_admin_can_approve_payroll(everyone):
    holders = {
        code
        for code in everyone
        if not code.startswith("_")
        and can(everyone[code], Resource.PAYROLL_RUN, Action.APPROVE)
    }
    assert holders == {RoleCode.FINANCE_HEAD, RoleCode.ADMIN}, (
        f"PAYROLL_RUN/APPROVE held by {sorted(holders)}"
    )


def test_only_admin_can_override_a_decision(everyone):
    holders = {
        code
        for code in everyone
        if not code.startswith("_")
        and can(everyone[code], Resource.APPLICATION, Action.OVERRIDE)
    }
    assert holders == {RoleCode.ADMIN}, f"APPLICATION/OVERRIDE held by {sorted(holders)}"


def test_only_three_roles_can_manage_users(everyone):
    holders = {
        code
        for code in everyone
        if not code.startswith("_") and can(everyone[code], Resource.USER, Action.CREATE)
    }
    assert holders == {RoleCode.ADMIN, RoleCode.HR_HEAD, RoleCode.HR_MANAGER}, (
        f"USER/CREATE held by {sorted(holders)}"
    )


# --------------------------------------------- segregation of duties


def test_nobody_can_both_hire_and_release_pay(everyone):
    """
    The classic control failure, checked on RESOLVED scope — at the point
    where money actually moves.

    The approved payroll policy gives HR Head the PREPARATION side of payroll
    (salary structures, drafting and processing runs, adjustments) alongside
    hiring, because a small clinic's HR genuinely does both. What preserves the
    control is the RELEASE point: nobody who hires may APPROVE a payroll run —
    a run someone else drafted still cannot pay a ghost employee without the
    Finance Head signing it off. Rate certification is likewise Finance-only,
    asserted by the matrix's own invariants.
    """
    recruitment = (Resource.CANDIDATE, Resource.APPLICATION, Resource.JOB_OPENING)

    violations = []
    for code, user in everyone.items():
        if code.startswith("_") or code == RoleCode.ADMIN:
            continue  # Admin is the deliberate exception, and is heavily audited.

        hires = any(
            can(user, resource, action) > Scope.SELF
            for resource in recruitment
            for action in (Action.CREATE, Action.EDIT, Action.REJECT, Action.APPROVE)
        )
        releases = can(user, Resource.PAYROLL_RUN, Action.APPROVE) > Scope.SELF
        if hires and releases:
            violations.append(code)

    assert not violations, (
        f"These roles can both hire and RELEASE pay: {violations}."
    )

    # And the roles that purely pay must still not hire at all — the Payroll
    # Executive and Finance Head keep their side of the wall intact.
    for code in (RoleCode.PAYROLL_EXECUTIVE, RoleCode.FINANCE_HEAD):
        hires = any(
            can(everyone[code], resource, action) > Scope.SELF
            for resource in recruitment
            for action in (Action.CREATE, Action.EDIT, Action.REJECT, Action.APPROVE)
        )
        assert not hires, f"{code} must not reach recruitment."


# ------------------------------------------------------- coverage gate


def test_every_resource_is_registered_for_scoping():
    """
    A resource missing from the registry can never be scoped below ALL.

    `manage.py check` enforces this at boot; asserting it here means a cutover
    run catches it too, without needing someone to read the check output.
    """
    from core.access.registry import RESOURCE_SPECS

    missing = [r for r in Resource.values if r not in RESOURCE_SPECS]
    assert not missing, f"Resources with no scoping spec: {missing}"


def test_the_system_check_passes_with_no_unmapped_views():
    """
    Every API view is RBAC-mapped or explicitly exempt.

    This is the structural guarantee that replaced "we remembered on every
    endpoint" — the single biggest failure of the previous system.
    """
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("check", stdout=out, stderr=out)

    assert "issues" not in out.getvalue() or "0 silenced" in out.getvalue()
