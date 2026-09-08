# Route in-flight pending requests through the manager stage.
#
# 0004 defaulted every existing row to the HR stage so nothing could strand
# while the two-step chain shipped. But a request that is still PENDING with
# no manager decision should follow the new rule — manager first — whenever
# the employee has a manager who can actually act. This mirrors
# services.initial_stage() against historical models: manager exists, has an
# active login, is not the applicant, is not the final authority already,
# and holds an approval grant.

from django.db import migrations

SCOPE_NONE = 0


def forwards(apps, schema_editor):
    LeaveRequest = apps.get_model("leave", "LeaveRequest")
    RolePermission = apps.get_model("accounts", "RolePermission")

    pending = LeaveRequest.objects.filter(
        status="pending",
        approval_stage="hr",
        manager_decided_at__isnull=True,
        is_active=True,
    ).select_related("employee__reporting_manager__user")

    for row in pending:
        manager = row.employee.reporting_manager
        if (
            manager is None
            or manager.user_id is None
            or manager.pk == row.employee_id
            or not manager.user.is_active
        ):
            continue
        codes = set(
            manager.user.user_roles.filter(is_active=True, role__is_active=True)
            .values_list("role__code", flat=True)
        )
        if not codes or "hr_head" in codes or "admin" in codes:
            continue
        holds_approve = (
            RolePermission.objects.filter(
                role__code__in=codes,
                role__is_active=True,
                resource="leave_request",
                action="approve",
                is_active=True,
            )
            .exclude(scope=SCOPE_NONE)
            .exists()
        )
        if not holds_approve:
            continue
        row.approval_stage = "manager"
        row.save(update_fields=["approval_stage", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("leave", "0004_leaverequest_approval_stage_and_more"),
        ("accounts", "0003_alter_rolepermission_resource_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
