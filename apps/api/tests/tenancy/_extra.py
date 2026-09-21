"""
The rest of the product, built twice.

`conftest._build` creates the ten kinds of object the hand-written matrix asks
about. That is enough for the matrix and nowhere near enough for the route
walker, which can only probe a route when a row of the right model exists in
BOTH organizations -- so with ten models it walked ten viewsets and skipped
offboarding, onboarding, hiring workflows, recruitment, payroll detail,
documents, devices, imports and audit **in silence**.

Silence is the problem. A walker reporting "no route served another
organization's row" while never asking about payslips is not evidence, and it
was on its way into a document that would have claimed it was. So this module
exists to make the walker's coverage a function of the fixture rather than of
what anyone remembered to list.

`scripts/verify_tenant_isolation.py` imports this too, and that is deliberate.
Both need the same thing -- an organization populated broadly enough that the
walk asks about payroll, recruitment and offboarding rather than about
departments six times -- and two definitions of it would drift. The one that
drifted would be the script, because the suite runs on every commit and the
script does not, which would leave the customer-facing report quietly claiming
more than it covered.

Rows are created straight through the ORM, deliberately: a service call would
apply business rules, refuse half of these states as invalid transitions, and
turn the fixture into an argument about workflow correctness instead of a
source of rows. The walker asks one question -- "does this row belong to
somebody else" -- and a row put there by hand answers it exactly as well.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone


def build_extra_rows(
    *,
    organization,
    slug,
    roles,
    department,
    location,
    designation,
    level,
    hr,
    hr_employee,
    worker_employee,
    spare_employee,
    leave_type,
    asset,
    candidate,
    payroll_run,
):
    """Everything the walker could not otherwise reach. Label -> instance."""
    from apps.assets.models import AssetAllocation
    from apps.attendance.models import (
        AttendanceDevice,
        EsslEmployeeLink,
        RegularizationRequest,
        ShiftRule,
    )
    from apps.audit.models import AuditAction, AuditLog
    from apps.employees.models import (
        DocumentCategory,
        DocumentType,
        EmployeeDocument,
        ProbationReview,
    )
    from apps.imports.models import ImportBatch
    from apps.itaccounts.models import CompanyEmailAccount
    from apps.leave.models import (
        Holiday,
        HolidayCalendar,
        HolidayWork,
        LeaveBalance,
        LeavePolicy,
        ShortLeave,
    )
    from apps.offboarding.models import (
        ClearanceCategory,
        ClearanceOwner,
        ClearanceTemplate,
        ExitClearanceItem,
        ExitType,
        ExitWorkflow,
        ResignationReason,
        ResignationRequest,
    )
    from apps.onboarding.models import (
        EmployeeLetter,
        EmployeeOnboarding,
        ItemKind,
        ItemStatus,
        LetterTemplate,
        LetterType,
        OnboardingItem,
        OnboardingStatus,
        OnboardingTemplate,
    )
    from apps.organization.models import Team
    from apps.payroll.models import (
        AdjustmentKind,
        ComponentType,
        InvestmentDeclaration,
        PayrollAdjustment,
        Payslip,
        SalaryComponent,
        SalaryStructure,
    )
    from apps.recruitment.models import Application, Interview, JobOpening, Offer
    from apps.workflows.models import (
        FeedbackForm,
        HiringWorkflow,
        StageKind,
        WorkflowStage,
    )

    rows: dict = {}

    # --- organisation structure -------------------------------------------
    rows["team"] = Team.objects.create(
        name="Payroll Team", code="PAY", department=department
    )

    # --- employee records --------------------------------------------------
    document_type = DocumentType.objects.create(
        name="PAN card", code="pan", category=DocumentCategory.IDENTITY
    )
    rows["document_type"] = document_type
    # A real (tiny) upload rather than a bare path string. The document
    # serializer reports the stored file's size, so a path naming a file that
    # is not there makes the detail route raise instead of answering -- and a
    # route that raises for BOTH organizations is a route the walker drops,
    # which would quietly remove employee documents from the evidence.
    rows["employee_document"] = EmployeeDocument.objects.create(
        employee=worker_employee,
        document_type=document_type,
        file=SimpleUploadedFile(
            "fixture.pdf", b"%PDF-1.4 fixture", content_type="application/pdf"
        ),
        original_filename="fixture.pdf",
        content_type="application/pdf",
        size_bytes=16,
    )
    rows["probation_review"] = ProbationReview.objects.create(
        employee=worker_employee, probation_end_date=dt.date(2024, 7, 1)
    )

    # --- assets ------------------------------------------------------------
    rows["asset_allocation"] = AssetAllocation.objects.create(
        asset=asset, employee=worker_employee, allocated_at=timezone.now()
    )

    # --- attendance --------------------------------------------------------
    rows["attendance_device"] = AttendanceDevice.objects.create(
        name="Front desk", serial_number=f"SN-{slug[:6].upper()}", location=location
    )
    rows["essl_link"] = EsslEmployeeLink.objects.create(
        essl_user_id=f"{abs(hash(slug)) % 9000 + 1000}", employee=worker_employee
    )
    rows["regularization"] = RegularizationRequest.objects.create(
        employee=worker_employee, date=dt.date(2025, 6, 4), reason="Missed punch"
    )
    # The organization's DEFAULT rule (location IS NULL), of which there is
    # exactly one per organization by constraint. An organization created by
    # the provisioning service already has it -- `seed_shift_rules` is one of
    # the configuration seeds -- so it is reused rather than duplicated, which
    # would violate `uniq_default_shift_rule`. An organization built by hand,
    # as the route walker's are, gets one created here exactly as before; for
    # the walk either is the same kind of row, one per organization.
    rows["shift_rule"] = ShiftRule.objects.filter(
        location__isnull=True
    ).first() or ShiftRule.objects.create(
        start_time=dt.time(9, 30), end_time=dt.time(18, 30)
    )

    # --- leave -------------------------------------------------------------
    calendar = HolidayCalendar.objects.create(name=f"{slug} calendar")
    rows["holiday_calendar"] = calendar
    rows["holiday"] = Holiday.objects.create(
        calendar=calendar, date=dt.date(2025, 8, 15), name="Independence Day"
    )
    rows["holiday_work"] = HolidayWork.objects.create(
        employee=worker_employee, date=dt.date(2025, 8, 15)
    )
    rows["leave_balance"] = LeaveBalance.objects.create(
        employee=worker_employee,
        leave_type=leave_type,
        year=2025,
        allocated=Decimal("12.0"),
    )
    rows["leave_policy"] = LeavePolicy.objects.create(
        leave_type=leave_type, name="Casual leave policy"
    )
    rows["short_leave"] = ShortLeave.objects.create(
        employee=worker_employee, date=dt.date(2025, 6, 5), hours=Decimal("2.0")
    )

    # --- payroll -----------------------------------------------------------
    component = SalaryComponent.objects.create(
        code="basic", name="Basic", component_type=ComponentType.EARNING
    )
    rows["salary_component"] = component
    structure = SalaryStructure.objects.create(
        employee=worker_employee,
        ctc_annual=Decimal("600000.00"),
        valid_from=dt.date(2024, 4, 1),
    )
    rows["salary_structure"] = structure
    rows["payslip"] = Payslip.objects.create(
        payroll_run=payroll_run, employee=worker_employee, salary_structure=structure
    )
    rows["payroll_adjustment"] = PayrollAdjustment.objects.create(
        employee=worker_employee,
        kind=AdjustmentKind.BONUS,
        label="Festival bonus",
        amount=Decimal("5000.00"),
        period_month=6,
        period_year=2025,
    )
    rows["investment_declaration"] = InvestmentDeclaration.objects.create(
        employee=worker_employee, financial_year="2025-2026"
    )

    # --- hiring workflow ---------------------------------------------------
    workflow = HiringWorkflow.objects.create(name="Standard hiring")
    rows["hiring_workflow"] = workflow
    stage = WorkflowStage.objects.create(
        workflow=workflow, name="Application", order=1, kind=StageKind.APPLICATION
    )
    rows["workflow_stage"] = stage
    rows["feedback_form"] = FeedbackForm.objects.create(name="Interview feedback")

    # --- recruitment -------------------------------------------------------
    job = JobOpening.objects.create(
        title="Support Executive",
        workflow=workflow,
        department=department,
        designation=designation,
        location=location,
        level=level,
        target_role=roles["employee"],
    )
    rows["job_opening"] = job
    application = Application.objects.create(
        candidate=candidate, job_opening=job, current_stage=stage
    )
    rows["application"] = application
    rows["interview"] = Interview.objects.create(
        application=application,
        stage=stage,
        interviewer=hr_employee,
        scheduled_at=timezone.make_aware(dt.datetime(2025, 7, 1, 10, 0)),
    )
    rows["offer"] = Offer.objects.create(
        application=application,
        offered_ctc=Decimal("450000.00"),
        joining_date=dt.date(2025, 8, 1),
    )
    rows["import_batch"] = ImportBatch.objects.create(
        platform="naukri",
        original_filename="candidates.csv",
        file_sha256=f"{abs(hash(slug)):064x}"[:64],
        uploaded_by=hr,
        job_opening=job,
    )

    # --- onboarding --------------------------------------------------------
    template = OnboardingTemplate.objects.create(name="New joiner")
    rows["onboarding_template"] = template
    # COMPLETED, not the default in_progress. `OnboardingGate` refuses every
    # route to an employee whose own checklist is still in progress with a
    # mandatory item outstanding, so leaving these at their defaults put the
    # fixture's worker behind the gate and turned unrelated tests 403 -- which
    # is the gate working correctly, on a person who joined in January 2024 and
    # has no business still being onboarded.
    onboarding = EmployeeOnboarding.objects.create(
        employee=worker_employee,
        template=template,
        joining_date=dt.date(2024, 1, 1),
        status=OnboardingStatus.COMPLETED,
        completed_at=timezone.now(),
    )
    rows["employee_onboarding"] = onboarding
    rows["onboarding_item"] = OnboardingItem.objects.create(
        onboarding=onboarding,
        title="Collect PAN",
        kind=ItemKind.DOCUMENT,
        status=ItemStatus.COMPLETED,
        completed_at=timezone.now(),
    )
    letter_template = LetterTemplate.objects.create(
        name="Appointment",
        letter_type=LetterType.APPOINTMENT,
        subject="Your appointment",
        body_html="<p>Welcome</p>",
    )
    rows["letter_template"] = letter_template
    rows["employee_letter"] = EmployeeLetter.objects.create(
        employee=worker_employee,
        letter_type=LetterType.APPOINTMENT,
        template=letter_template,
        subject="Your appointment",
    )

    # --- IT accounts -------------------------------------------------------
    rows["company_email_account"] = CompanyEmailAccount.objects.create(
        employee=worker_employee, email_address=f"worker@{slug}.example"
    )

    # --- offboarding -------------------------------------------------------
    # On the SPARE employee, so nothing above is exiting the company while the
    # rest of the fixture assumes it is not.
    rows["clearance_template"] = ClearanceTemplate.objects.create(name="Standard exit")
    resignation = ResignationRequest.objects.create(
        employee=spare_employee,
        requested_last_working_date=dt.date(2025, 9, 30),
        reason=ResignationReason.PERSONAL,
        submitted_by=hr,
    )
    rows["resignation"] = resignation
    exit_workflow = ExitWorkflow.objects.create(
        employee=spare_employee,
        exit_type=ExitType.RESIGNATION,
        resignation=resignation,
        expected_last_working_date=dt.date(2025, 9, 30),
        initiated_by=hr,
    )
    rows["exit_workflow"] = exit_workflow
    rows["exit_clearance_item"] = ExitClearanceItem.objects.create(
        exit_workflow=exit_workflow,
        title="Return laptop",
        category=ClearanceCategory.ASSETS,
        owner=ClearanceOwner.IT,
    )

    # --- audit -------------------------------------------------------------
    # Written with an explicit organization because nothing else stamps one
    # yet. That is the point: without a row on both sides the walker cannot
    # ask whether one organization reads another's audit trail, and the brief
    # names audit logs among the things it must never reach.
    rows["audit_log"] = AuditLog.objects.create(
        organization=organization,
        action=AuditAction.CREATE,
        entity_type="employees.Employee",
        entity_id=str(worker_employee.pk),
        entity_label=worker_employee.employee_code,
        actor=hr,
        actor_email=hr.email,
        subject_employee=worker_employee,
    )

    return rows
