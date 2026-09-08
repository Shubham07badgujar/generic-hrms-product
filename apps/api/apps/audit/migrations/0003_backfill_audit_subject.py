"""
Backfill `subject_employee` on audit rows written before the field existed.

Without this, every Department Head's audit view is empty on the day this
ships — the scoping works perfectly and finds nothing, which is the most
confusing possible failure. Only the entity types that can be resolved from
the stored `entity_id` alone are backfilled; everything else keeps NULL and
stays visible at ALL scope, which is what it was before.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    AuditLog = apps.get_model("audit", "AuditLog")
    Employee = apps.get_model("employees", "Employee")

    # An Employee row's entity_id IS the employee id — resolvable directly.
    employee_ids = set(str(pk) for pk in Employee.objects.values_list("pk", flat=True))
    rows = AuditLog.objects.filter(
        entity_type="employees.Employee", subject_employee__isnull=True
    ).only("id", "entity_id")

    updates = []
    for row in rows.iterator(chunk_size=2000):
        if row.entity_id in employee_ids:
            row.subject_employee_id = row.entity_id
            updates.append(row)

    if updates:
        AuditLog.objects.bulk_update(updates, ["subject_employee"], batch_size=1000)

    # Payslips reach an employee in one hop and are the other high-volume
    # employee-specific type. Resolved in bulk rather than per row.
    Payslip = apps.get_model("payroll", "Payslip")
    payslip_owner = dict(
        (str(pk), employee_id)
        for pk, employee_id in Payslip.objects.values_list("pk", "employee_id")
    )
    payslip_rows = AuditLog.objects.filter(
        entity_type="payroll.Payslip", subject_employee__isnull=True
    ).only("id", "entity_id")

    updates = []
    for row in payslip_rows.iterator(chunk_size=2000):
        owner = payslip_owner.get(row.entity_id)
        if owner:
            row.subject_employee_id = owner
            updates.append(row)

    if updates:
        AuditLog.objects.bulk_update(updates, ["subject_employee"], batch_size=1000)


def noop(apps, schema_editor):
    """Reversing leaves the data. Clearing it would destroy scoping information."""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_auditlog_reason_auditlog_subject_employee_and_more"),
        ("payroll", "0001_initial"),
        ("employees", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, noop)]
