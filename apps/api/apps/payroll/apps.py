from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payroll"
    label = "payroll"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            EmployeeLoan,
            EmployeePackage,
            InvestmentDeclaration,
            PackageDeferral,
            PackagePeriod,
            PayrollAdjustment,
            PayrollRun,
            Payslip,
            ReimbursementClaim,
            SalaryComponent,
            SalaryStructure,
            SalaryStructureLine,
        )

        # Everything that decides what someone is paid, or records that they
        # were paid it, is audited. Payslip LINES are not registered separately:
        # a payslip is created and frozen as a unit, so per-line events would be
        # noise around an event that is already captured.
        register(SalaryComponent)
        register(SalaryStructure)
        register(SalaryStructureLine)
        register(PayrollRun)
        register(Payslip)
        register(PayrollAdjustment)
        register(EmployeeLoan)
        register(ReimbursementClaim)
        # A declaration holds the employee's private financial affairs. The
        # trail records that it was submitted, verified and by whom — never the
        # figures, which are visible to Finance in the app under permission and
        # have no business being copied into a log every HR role can read.
        register(InvestmentDeclaration, redact={"declarations", "proofs"})
        # Package schedules move real money on future dates — every field
        # change is auditable, and the services add explicit lifecycle events
        # (created, activated, eligible, approved, rejected, rescheduled) on
        # top of the generic diffs.
        register(EmployeePackage)
        register(PackagePeriod)
        register(PackageDeferral)
