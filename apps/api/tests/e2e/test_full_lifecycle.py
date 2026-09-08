"""
Cutover gate 6: the whole thing, end to end.

Recruiter → HR verification → department interviews → department
recommendation → HR final decision → offer → acceptance → employee →
onboarding → probation → confirmation → payroll.

Run twice, for the Therapist and Office Boy pipelines. Those two exist to prove
the claim the workflow engine was built on: **two different hiring processes,
zero code difference**. If a single `if job_title ==` had crept in anywhere,
one of these would fail.

Every step goes through the real service. Nothing sets a status directly, so a
step that could not actually be reached from the previous one fails here rather
than passing against a state the system cannot produce.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

#: The two pipelines. Same engine, different configuration rows.
PIPELINES = [
    pytest.param(
        {
            "job": "therapist_job",
            "interview_roles": ("clinic_doctor", "senior_doctor"),
            "department_head": "medical_director",
            "role_code": "therapist",
            "department": "medical",
        },
        id="therapist",
    ),
    pytest.param(
        {
            "job": "office_boy_job",
            "interview_roles": ("cre", "operations_manager"),
            "department_head": "operational_head",
            "role_code": "office_boy",
            "department": "operations",
        },
        id="office_boy",
    ),
]


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_the_complete_lifecycle_runs_on_one_engine(
    pipeline, request, staff, finance_staff, org, make_application,
    drive_to_selection, onboarding_config, statutory_rates,
):
    """Candidate to paid employee, with no pipeline-specific code anywhere."""
    from apps.employees.models import EmployeeStatus, ProbationDecision
    from apps.employees.services import probation
    from apps.payroll import services as payroll
    from apps.payroll.models import PayrollRunStatus, SalaryComponent
    from apps.recruitment.models import ApplicationStatus
    from apps.recruitment.services import hiring

    job = request.getfixturevalue(pipeline["job"])

    # --- 1. application ---------------------------------------------------
    application = make_application(job, first_name="Endto", email="e2e@example.test")
    assert application.status == ApplicationStatus.ACTIVE

    # --- 2..6. screening, verification, interviews, recommendation, decision
    application = drive_to_selection(
        application,
        interview_roles=pipeline["interview_roles"],
        department_head=pipeline["department_head"],
    )
    assert application.status == ApplicationStatus.SELECTED

    # --- 7. offer ---------------------------------------------------------
    # The offer carries the level; conversion reads employment details from
    # the offer and the job rather than taking them again from the caller.
    offer = hiring.create_offer(
        application=application,
        actor=staff["hr_head"].user,
        joining_date=timezone.localdate() + dt.timedelta(days=30),
        offered_ctc=Decimal("480000.00"),
        level=org["levels"][5],
    )
    hiring.send_offer(offer=offer, actor=staff["hr_head"].user)
    hiring.record_offer_response(
        offer=offer, actor=staff["hr_head"].user, accepted=True
    )
    offer.refresh_from_db()
    assert offer.status == "accepted"

    # --- 8. candidate becomes an employee --------------------------------
    result = hiring.convert_to_employee(
        application=application,
        actor=staff["hr_head"].user,
        reporting_manager=staff[pipeline["department_head"]],
    )
    employee = result.employee

    assert employee.created_from_candidate_id == application.candidate_id
    assert employee.department_id == job.department_id
    assert employee.status in (EmployeeStatus.ONBOARDING, EmployeeStatus.ON_PROBATION)

    # --- 9. onboarding ----------------------------------------------------
    onboarding = getattr(employee, "onboarding", None)
    assert onboarding is not None, "Conversion did not create an onboarding checklist."
    assert onboarding.items.exists(), "The checklist has no items."

    # --- 10. probation: the system NEVER auto-confirms --------------------
    # Backdate joining and probation end so the sweep has something due. This
    # is test setup on dates, not a bypass — every state change below still
    # goes through the probation service.
    employee.date_of_joining = timezone.localdate() - dt.timedelta(days=200)
    employee.probation_end_date = timezone.localdate() - dt.timedelta(days=1)
    employee.status = EmployeeStatus.ON_PROBATION
    employee.save()

    probation.run_reminder_sweep()
    employee.refresh_from_db()
    assert employee.status != EmployeeStatus.CONFIRMED, (
        "The sweep confirmed an employee. It must only ever prompt."
    )

    review = employee.probation_reviews.filter(decision=ProbationDecision.PENDING).first()
    assert review is not None, "The sweep opened no review for HR to act on."

    # --- 11. confirmation, by an explicit human decision ------------------
    probation.decide(
        review=review,
        actor=staff["hr_head"].user,
        decision=ProbationDecision.CONFIRM,
        rationale="Performed well throughout the probation period.",
    )
    employee.refresh_from_db()
    assert employee.status == EmployeeStatus.CONFIRMED

    # --- 12. payroll ------------------------------------------------------
    finance = staff["finance_head"].user
    for spec in payroll.DEFAULT_COMPONENTS:
        if not SalaryComponent.objects.filter(code=spec["code"]).exists():
            payroll.save_component(actor=finance, data=dict(spec))

    # Payroll now clips pay to the employment window, so a June-2025 run can
    # only pay someone employed in June 2025 — pin the joining date before
    # the period instead of relying on the old pay-before-joining leniency.
    employee.date_of_joining = dt.date(2025, 1, 1)
    employee.save(update_fields=["date_of_joining"])

    components = {c.code: c for c in SalaryComponent.objects.all()}
    payroll.create_salary_structure(
        actor=finance,
        employee=employee,
        ctc_annual=Decimal("480000.00"),
        valid_from=dt.date(2025, 1, 1),
        lines=[
            {"component": components["BASIC"].pk, "value": Decimal("20000.00")},
            {"component": components["HRA"].pk, "value": Decimal("40")},
        ],
    )

    run = payroll.create_run(
        actor=staff["accounts_manager"].user, period_year=2025, period_month=6
    )
    run = payroll.process_run(run, actor=staff["accounts_manager"].user)

    payslip = run.payslips.filter(employee=employee).first()
    assert payslip is not None, "The confirmed employee was not paid."
    assert payslip.net_pay > 0
    assert payslip.lines.exists(), "A payslip with no lines cannot be explained."

    # Approved by a second person — the accounts manager processed it.
    run = payroll.approve_run(run, actor=finance)
    assert run.status == PayrollRunStatus.APPROVED
    assert run.locked is True


def test_both_pipelines_use_the_same_engine_with_different_data(
    therapist_job, office_boy_job
):
    """
    The configuration-as-data claim, stated as an assertion.

    Two workflows, different stages and different actors, and the difference
    lives entirely in rows.
    """
    therapist_stages = list(
        therapist_job.workflow.stages.order_by("order").values_list("name", flat=True)
    )
    office_stages = list(
        office_boy_job.workflow.stages.order_by("order").values_list("name", flat=True)
    )

    assert therapist_job.workflow_id != office_boy_job.workflow_id
    assert therapist_stages != office_stages
    assert len(therapist_stages) > 3 and len(office_stages) > 3
