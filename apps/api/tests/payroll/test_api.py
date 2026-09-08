"""
Payroll API: authorization, scoping and the gates as seen through HTTP.

The service tests prove the rules hold when called directly. These prove the
same rules hold when called the way an attacker would — over the wire, with a
token, bypassing whatever the UI would or would not have shown.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.payroll import services
from apps.payroll.models import PayrollRunStatus
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

RUNS = "/api/v1/payroll/runs/"
PAYSLIPS = "/api/v1/payslips/"
STRUCTURES = "/api/v1/payroll/structures/"
COMPONENTS = "/api/v1/payroll/components/"
RULE_SETS = "/api/v1/payroll/rule-sets/"
MINE = "/api/v1/payroll/me/"


def rows(response):
    """Rows from either a paginated envelope or a bare list (unpaginated views)."""
    body = response.json()
    return body if isinstance(body, list) else body.get("data", [])


# ------------------------------------------------------------ visibility


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("finance_head", 200),
        ("accounts_manager", 200),
        ("payroll_executive", 200),
        # HR Head co-owns payroll operations: prepares and reviews runs.
        # APPROVING a run (releasing the money) remains Finance + Admin only —
        # that segregation is asserted by the matrix invariants.
        ("hr_head", 200),
        ("recruiter", 403),
        ("therapist", 403),
    ],
)
def test_who_may_list_payroll_runs(auth, finance, make_user, roles, role, expected):
    user = finance[role].user if role in finance else make_user(role)
    assert auth(user).get(RUNS).status_code == expected


def test_a_recruiter_cannot_reach_payroll_at_all(auth, make_user):
    """
    Segregation of duties, asserted from the outside.

    One person able to both hire someone and pay them is the classic control
    failure; the matrix makes the two permission sets disjoint and this proves
    the API honours it.
    """
    client = auth(make_user("recruiter"))

    assert client.get(RUNS).status_code == 403
    assert client.post(RUNS, {"period_year": 2025, "period_month": 6}, format="json").status_code == 403


def test_payslips_are_scoped_to_the_caller(auth, finance, processed_run):
    """An employee sees their own payslip and nobody else's."""
    therapist = auth(finance["therapist"].user).get(PAYSLIPS)
    everyone = auth(finance["finance_head"].user).get(PAYSLIPS)

    assert therapist.status_code == 200
    codes = {row["employee_code"] for row in rows(therapist)}
    assert codes == {finance["therapist"].employee_code}

    assert len({row["employee_code"] for row in rows(everyone)}) == processed_run.payslips.count()


def test_another_employees_payslip_is_not_retrievable_by_id(auth, finance, processed_run):
    """
    Out of scope must behave as if the row does not exist.

    A 403 would confirm the payslip exists, which is itself a disclosure —
    someone probing ids could map the payroll.
    """
    other = processed_run.payslips.exclude(employee=finance["therapist"]).first()

    response = auth(finance["therapist"].user).get(f"{PAYSLIPS}{other.pk}/")

    assert response.status_code == 404


def test_the_component_catalogue_is_not_readable_by_an_ordinary_employee(auth, finance, components):
    """
    Everyone holds SALARY/VIEW at SELF for their own payslip. That must not
    also hand them the organisation's pay-structure catalogue, which describes
    how everyone else's pay is composed.
    """
    employee = auth(finance["therapist"].user).get(COMPONENTS)
    finance_view = auth(finance["finance_head"].user).get(COMPONENTS)

    assert employee.status_code == 200
    assert rows(employee) == []
    assert len(rows(finance_view)) > 0


def test_self_service_resolves_mine_from_the_token(auth, finance, processed_run):
    """No client-supplied employee id is involved, so there is nothing to tamper with."""
    response = auth(finance["therapist"].user).get(MINE)

    assert response.status_code == 200
    body = response.json()
    assert body["structure"] is not None
    assert all(
        p["employee_code"] == finance["therapist"].employee_code for p in body["payslips"]
    )


# ------------------------------------------------------------- the gates


def test_approval_is_refused_over_http_for_unverified_rates(auth, finance, processed_run):
    response = auth(finance["finance_head"].user).post(
        f"{RUNS}{processed_run.pk}/approve/", {}, format="json"
    )

    assert response.status_code == 400
    assert "not verified" in str(response.json())


def test_the_detail_payload_reports_the_same_blockers_the_gate_enforces(
    auth, finance, processed_run
):
    """
    What the UI is told and what the server enforces come from one function.

    If these could drift, a user would see an enabled button and a refusal.
    """
    response = auth(finance["finance_head"].user).get(f"{RUNS}{processed_run.pk}/")
    body = response.json()

    assert body["can_approve"] is False
    statutory = [b for b in body["blockers"] if b["gate"] == "statutory"]
    assert len(statutory) == len(services.unverified_rule_sets(processed_run))


def test_can_approve_becomes_true_once_rates_are_verified(auth, finance, approvable_run):
    response = auth(finance["finance_head"].user).get(f"{RUNS}{approvable_run.pk}/")
    body = response.json()

    assert body["blockers"] == []
    assert body["can_approve"] is True


def test_can_approve_is_false_for_the_person_who_processed_the_run(
    auth, finance, approvable_run, roles
):
    """The UI must not offer a button the server is going to refuse."""
    from apps.accounts.models import UserRole

    processor = finance["payroll_executive"].user
    UserRole.objects.create(user=processor, role=roles["finance_head"])
    bind_membership(processor)

    body = auth(processor).get(f"{RUNS}{approvable_run.pk}/").json()

    assert body["can_approve"] is False
    assert any(b["gate"] == "segregation" for b in body["blockers"])


def test_payroll_staff_cannot_approve_even_a_clean_run(auth, finance, approvable_run):
    response = auth(finance["payroll_executive"].user).post(
        f"{RUNS}{approvable_run.pk}/approve/", {}, format="json"
    )
    assert response.status_code == 403


def test_finance_head_can_approve_over_http(auth, finance, approvable_run):
    response = auth(finance["finance_head"].user).post(
        f"{RUNS}{approvable_run.pk}/approve/", {}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["status"] == PayrollRunStatus.APPROVED
    assert response.json()["locked"] is True


# ---------------------------------------------------------------- outputs


def test_a_bank_advice_cannot_be_produced_for_an_unapproved_run(auth, finance, processed_run):
    """
    This file moves money.

    Producing one from a draft invites paying figures nobody signed off, so the
    refusal is on the endpoint rather than left to whoever is looking at it.
    """
    response = auth(finance["finance_head"].user).get(f"{RUNS}{processed_run.pk}/neft/")

    assert response.status_code == 400


def test_an_approved_run_produces_a_bank_advice(auth, finance, approvable_run):
    services.approve_run(approvable_run, actor=finance["finance_head"].user)

    response = auth(finance["finance_head"].user).get(f"{RUNS}{approvable_run.pk}/neft/")

    assert response.status_code == 200
    body = response.content.decode()
    assert body.startswith("EMPLOYEE_CODE\tNAME\tBANK_ACCOUNT")


def test_the_salary_register_is_a_real_xlsx(auth, finance, processed_run):
    response = auth(finance["finance_head"].user).get(f"{RUNS}{processed_run.pk}/register/")

    assert response.status_code == 200
    # A .xlsx is a zip; anything else means the workbook was not written.
    assert response.content[:2] == b"PK"


def test_a_payslip_pdf_is_a_real_pdf(auth, finance, processed_run):
    payslip = processed_run.payslips.get(employee=finance["therapist"])

    response = auth(finance["therapist"].user).get(f"{PAYSLIPS}{payslip.pk}/pdf/")

    assert response.status_code == 200
    assert response.content[:5] == b"%PDF-"


def test_an_export_is_audited(auth, finance, processed_run):
    """Exports carry payroll data out of the system, so each one is recorded."""
    from apps.audit.models import AuditAction, AuditLog

    auth(finance["finance_head"].user).get(f"{RUNS}{processed_run.pk}/register/")

    assert AuditLog.objects.filter(
        action=AuditAction.EXPORT, entity_id=str(processed_run.pk)
    ).exists()


# ----------------------------------------------------------- rule sets


def test_only_finance_head_may_verify_a_statutory_rate_set(auth, finance, rate_sets, make_user):
    """
    Certification is a professional judgement, not an administrative capability.

    Admin can edit a draft rate set but must never be able to certify one.
    """
    rule_set = rate_sets[0]
    path = f"{RULE_SETS}{rule_set.pk}/verify/"

    assert auth(make_user("admin")).post(path, {}, format="json").status_code == 403
    assert auth(finance["accounts_manager"].user).post(path, {}, format="json").status_code == 403
    assert auth(finance["payroll_executive"].user).post(path, {}, format="json").status_code == 403


def test_an_ordinary_employee_cannot_see_statutory_rate_sets(auth, finance, rate_sets):
    assert auth(finance["therapist"].user).get(RULE_SETS).status_code == 403


# ------------------------------------------------------- salary structures


def test_creating_a_structure_for_an_employee_outside_scope_is_refused(
    auth, finance, components, make_user, org
):
    """
    A therapist holds SALARY/VIEW at SELF, and cannot create a structure at all.
    """
    response = auth(finance["therapist"].user).post(
        STRUCTURES,
        {
            "employee": str(finance["finance_head"].pk),
            "ctc_annual": "900000.00",
            "valid_from": "2025-04-01",
            "lines": [{"component": str(components["BASIC"].pk), "value": "50000.00"}],
        },
        format="json",
    )

    assert response.status_code == 403


def test_a_revision_closes_the_previous_structure(auth, finance, components, salaried):
    """Two open structures would make "current salary" ambiguous."""
    employee = finance["therapist"]

    response = auth(finance["finance_head"].user).post(
        STRUCTURES,
        {
            "employee": str(employee.pk),
            "ctc_annual": "720000.00",
            "valid_from": "2025-07-01",
            "revision_reason": "Annual increment",
            "lines": [
                {"component": str(components["BASIC"].pk), "value": "30000.00"},
                {"component": str(components["HRA"].pk), "value": "40"},
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    open_structures = employee.salary_structures.filter(valid_to__isnull=True, is_active=True)
    assert open_structures.count() == 1
    assert open_structures.first().valid_from.isoformat() == "2025-07-01"


def test_a_percentage_component_resolves_against_its_base(auth, finance, components, salaried):
    """HRA at 40% of a 30,000 Basic must be 12,000, whatever order the lines arrive in."""
    response = auth(finance["finance_head"].user).post(
        STRUCTURES,
        {
            "employee": str(finance["therapist"].pk),
            "ctc_annual": "720000.00",
            "valid_from": "2025-08-01",
            "lines": [
                # HRA deliberately FIRST, so a single-pass implementation would
                # resolve it against a Basic that does not exist yet.
                {"component": str(components["HRA"].pk), "value": "40"},
                {"component": str(components["BASIC"].pk), "value": "30000.00"},
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    lines = {line["component_code"]: line["monthly_amount"] for line in response.json()["lines"]}
    assert Decimal(lines["HRA"]) == Decimal("12000.00")


def test_an_impossible_period_is_a_400_not_a_500(auth, finance):
    """A month outside 1-12 must be refused by validation, never left to the
    database check constraint — that surfaces as a 500, not a 400."""
    client = auth(finance["payroll_executive"].user)
    for payload in ({"period_year": 2026, "period_month": 13},
                    {"period_year": 2026, "period_month": 0},
                    {"period_year": 12, "period_month": 6}):
        response = client.post(RUNS, payload, format="json")
        assert response.status_code == 400, (payload, response.status_code)
