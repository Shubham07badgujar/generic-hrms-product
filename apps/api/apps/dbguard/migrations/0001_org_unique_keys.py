"""UNIQUE (id, organization_id) on the 41 tables an organization-aware FK points at."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_alter_role_code_alter_role_name_and_more'),
        ('assets', '0003_alter_asset_asset_tag_alter_assetcategory_code_and_more'),
        ('attendance', '0004_orgattendanceintegration'),
        ('audit', '0002_initial'),
        ('employees', '0005_alter_employee_photo'),
        ('imports', '0003_employee_import'),
        ('itaccounts', '0002_alter_companyemailaccount_email_address_and_more'),
        ('leave', '0002_alter_leavetype_code_and_more'),
        ('notifications', '0001_initial'),
        ('offboarding', '0001_initial'),
        ('onboarding', '0002_remove_lettertemplate_uniq_default_letter_template_per_type_and_more'),
        ('organization', '0006_organization_lifecycle_timestamps'),
        ('payroll', '0003_remove_payrollrun_uniq_payroll_run_per_period_and_more'),
        ('platform', '0002_support_grant'),
        ('recruitment', '0003_alter_candidate_resume_alter_offer_letter_pdf'),
        ('reporting', '0003_metricsnapshot_label_metricsnapshot_sequence'),
        ('statutory', '0001_initial'),
        ('workflows', '0002_alter_feedbackform_name_alter_hiringworkflow_name_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_role" ADD CONSTRAINT "dbg_uq_accounts_role" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "accounts_role" DROP CONSTRAINT IF EXISTS "dbg_uq_accounts_role";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" ADD CONSTRAINT "dbg_uq_assets_asset" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "assets_asset" DROP CONSTRAINT IF EXISTS "dbg_uq_assets_asset";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetcategory" ADD CONSTRAINT "dbg_uq_assets_assetcategory" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "assets_assetcategory" DROP CONSTRAINT IF EXISTS "dbg_uq_assets_assetcategory";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancedevice" ADD CONSTRAINT "dbg_uq_attendance_attendancedevice" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "attendance_attendancedevice" DROP CONSTRAINT IF EXISTS "dbg_uq_attendance_attendancedevice";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_documenttype" ADD CONSTRAINT "dbg_uq_employees_documenttype" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "employees_documenttype" DROP CONSTRAINT IF EXISTS "dbg_uq_employees_documenttype";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_uq_employees_employee" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_uq_employees_employee";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" ADD CONSTRAINT "dbg_uq_employees_employeedocument" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "employees_employeedocument" DROP CONSTRAINT IF EXISTS "dbg_uq_employees_employeedocument";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importbatch" ADD CONSTRAINT "dbg_uq_imports_importbatch" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "imports_importbatch" DROP CONSTRAINT IF EXISTS "dbg_uq_imports_importbatch";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaycalendar" ADD CONSTRAINT "dbg_uq_leave_holidaycalendar" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "leave_holidaycalendar" DROP CONSTRAINT IF EXISTS "dbg_uq_leave_holidaycalendar";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" ADD CONSTRAINT "dbg_uq_leave_leavepolicy" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "leave_leavepolicy" DROP CONSTRAINT IF EXISTS "dbg_uq_leave_leavepolicy";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" ADD CONSTRAINT "dbg_uq_leave_leaverequest" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "leave_leaverequest" DROP CONSTRAINT IF EXISTS "dbg_uq_leave_leaverequest";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetype" ADD CONSTRAINT "dbg_uq_leave_leavetype" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "leave_leavetype" DROP CONSTRAINT IF EXISTS "dbg_uq_leave_leavetype";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notification" ADD CONSTRAINT "dbg_uq_notifications_notification" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "notifications_notification" DROP CONSTRAINT IF EXISTS "dbg_uq_notifications_notification";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplate" ADD CONSTRAINT "dbg_uq_offboarding_clearancetemplate" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "offboarding_clearancetemplate" DROP CONSTRAINT IF EXISTS "dbg_uq_offboarding_clearancetemplate";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplateitem" ADD CONSTRAINT "dbg_uq_offboarding_clearancetemplateitem" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "offboarding_clearancetemplateitem" DROP CONSTRAINT IF EXISTS "dbg_uq_offboarding_clearancetemplateitem";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" ADD CONSTRAINT "dbg_uq_offboarding_exitworkflow" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "offboarding_exitworkflow" DROP CONSTRAINT IF EXISTS "dbg_uq_offboarding_exitworkflow";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_resignationrequest" ADD CONSTRAINT "dbg_uq_offboarding_resignationrequest" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "offboarding_resignationrequest" DROP CONSTRAINT IF EXISTS "dbg_uq_offboarding_resignationrequest";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" ADD CONSTRAINT "dbg_uq_onboarding_employeeletter" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "onboarding_employeeletter" DROP CONSTRAINT IF EXISTS "dbg_uq_onboarding_employeeletter";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" ADD CONSTRAINT "dbg_uq_onboarding_employeeonboarding" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "onboarding_employeeonboarding" DROP CONSTRAINT IF EXISTS "dbg_uq_onboarding_employeeonboarding";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_lettertemplate" ADD CONSTRAINT "dbg_uq_onboarding_lettertemplate" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "onboarding_lettertemplate" DROP CONSTRAINT IF EXISTS "dbg_uq_onboarding_lettertemplate";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplate" ADD CONSTRAINT "dbg_uq_onboarding_onboardingtemplate" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "onboarding_onboardingtemplate" DROP CONSTRAINT IF EXISTS "dbg_uq_onboarding_onboardingtemplate";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" ADD CONSTRAINT "dbg_uq_onboarding_onboardingtemplateitem" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "onboarding_onboardingtemplateitem" DROP CONSTRAINT IF EXISTS "dbg_uq_onboarding_onboardingtemplateitem";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" ADD CONSTRAINT "dbg_uq_organization_department" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "organization_department" DROP CONSTRAINT IF EXISTS "dbg_uq_organization_department";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_designation" ADD CONSTRAINT "dbg_uq_organization_designation" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "organization_designation" DROP CONSTRAINT IF EXISTS "dbg_uq_organization_designation";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_employeelevel" ADD CONSTRAINT "dbg_uq_organization_employeelevel" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "organization_employeelevel" DROP CONSTRAINT IF EXISTS "dbg_uq_organization_employeelevel";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_location" ADD CONSTRAINT "dbg_uq_organization_location" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "organization_location" DROP CONSTRAINT IF EXISTS "dbg_uq_organization_location";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" ADD CONSTRAINT "dbg_uq_organization_team" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "organization_team" DROP CONSTRAINT IF EXISTS "dbg_uq_organization_team";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" ADD CONSTRAINT "dbg_uq_payroll_employeepackage" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_employeepackage" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_employeepackage";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" ADD CONSTRAINT "dbg_uq_payroll_payrolladjustment" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_payrolladjustment" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_payrolladjustment";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrollrun" ADD CONSTRAINT "dbg_uq_payroll_payrollrun" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_payrollrun" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_payrollrun";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ADD CONSTRAINT "dbg_uq_payroll_payslip" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_payslip" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_payslip";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarycomponent" ADD CONSTRAINT "dbg_uq_payroll_salarycomponent" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_salarycomponent" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_salarycomponent";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructure" ADD CONSTRAINT "dbg_uq_payroll_salarystructure" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "payroll_salarystructure" DROP CONSTRAINT IF EXISTS "dbg_uq_payroll_salarystructure";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" ADD CONSTRAINT "dbg_uq_recruitment_application" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "recruitment_application" DROP CONSTRAINT IF EXISTS "dbg_uq_recruitment_application";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidate" ADD CONSTRAINT "dbg_uq_recruitment_candidate" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "recruitment_candidate" DROP CONSTRAINT IF EXISTS "dbg_uq_recruitment_candidate";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" ADD CONSTRAINT "dbg_uq_recruitment_interview" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "recruitment_interview" DROP CONSTRAINT IF EXISTS "dbg_uq_recruitment_interview";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_uq_recruitment_jobopening" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_uq_recruitment_jobopening";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" ADD CONSTRAINT "dbg_uq_recruitment_stagedecision" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "recruitment_stagedecision" DROP CONSTRAINT IF EXISTS "dbg_uq_recruitment_stagedecision";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_feedbackform" ADD CONSTRAINT "dbg_uq_workflows_feedbackform" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "workflows_feedbackform" DROP CONSTRAINT IF EXISTS "dbg_uq_workflows_feedbackform";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_hiringworkflow" ADD CONSTRAINT "dbg_uq_workflows_hiringworkflow" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "workflows_hiringworkflow" DROP CONSTRAINT IF EXISTS "dbg_uq_workflows_hiringworkflow";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" ADD CONSTRAINT "dbg_uq_workflows_workflowstage" UNIQUE ("id", "organization_id");',
            reverse_sql='ALTER TABLE "workflows_workflowstage" DROP CONSTRAINT IF EXISTS "dbg_uq_workflows_workflowstage";',
        ),
    ]
