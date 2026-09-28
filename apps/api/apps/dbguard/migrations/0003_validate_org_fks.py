"""Validate every composite FK. Fails if any existing row already points across organizations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('dbguard', '0002_org_composite_fks'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_rolepermission" VALIDATE CONSTRAINT "dbg_fk_accounts_rolepermission_role_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_userrole" VALIDATE CONSTRAINT "dbg_fk_accounts_userrole_role_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" VALIDATE CONSTRAINT "dbg_fk_assets_asset_category_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" VALIDATE CONSTRAINT "dbg_fk_assets_asset_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetallocation" VALIDATE CONSTRAINT "dbg_fk_assets_assetallocation_asset_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetallocation" VALIDATE CONSTRAINT "dbg_fk_assets_assetallocation_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetmaintenancelog" VALIDATE CONSTRAINT "dbg_fk_assets_assetmaintenancelog_asset_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancedevice" VALIDATE CONSTRAINT "dbg_fk_attendance_attendancedevice_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancerecord" VALIDATE CONSTRAINT "dbg_fk_attendance_attendancerecord_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslemployeelink" VALIDATE CONSTRAINT "dbg_fk_attendance_esslemployeelink_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslemployeelink" VALIDATE CONSTRAINT "dbg_fk_attendance_esslemployeelink_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_rawpunch" VALIDATE CONSTRAINT "dbg_fk_attendance_rawpunch_device_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_rawpunch" VALIDATE CONSTRAINT "dbg_fk_attendance_rawpunch_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_regularizationrequest" VALIDATE CONSTRAINT "dbg_fk_attendance_regularizationrequest_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_shiftrule" VALIDATE CONSTRAINT "dbg_fk_attendance_shiftrule_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_emergencycontact" VALIDATE CONSTRAINT "dbg_fk_employees_emergencycontact_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_created_from_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_designation_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_level_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_reporting_manager_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" VALIDATE CONSTRAINT "dbg_fk_employees_employee_team_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeaddress" VALIDATE CONSTRAINT "dbg_fk_employees_employeeaddress_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" VALIDATE CONSTRAINT "dbg_fk_employees_employeedocument_document_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" VALIDATE CONSTRAINT "dbg_fk_employees_employeedocument_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeeducation" VALIDATE CONSTRAINT "dbg_fk_employees_employeeeducation_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeexperience" VALIDATE CONSTRAINT "dbg_fk_employees_employeeexperience_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" VALIDATE CONSTRAINT "dbg_fk_employees_probationreview_confirmation_letter_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" VALIDATE CONSTRAINT "dbg_fk_employees_probationreview_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" VALIDATE CONSTRAINT "dbg_fk_employees_probationreview_reviewer_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importbatch" VALIDATE CONSTRAINT "dbg_fk_imports_importbatch_job_opening_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" VALIDATE CONSTRAINT "dbg_fk_imports_importrow_batch_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" VALIDATE CONSTRAINT "dbg_fk_imports_importrow_created_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" VALIDATE CONSTRAINT "dbg_fk_imports_importrow_matched_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "itaccounts_companyemailaccount" VALIDATE CONSTRAINT "dbg_fk_itaccounts_companyemailaccount_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holiday" VALIDATE CONSTRAINT "dbg_fk_leave_holiday_calendar_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaycalendar" VALIDATE CONSTRAINT "dbg_fk_leave_holidaycalendar_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaywork" VALIDATE CONSTRAINT "dbg_fk_leave_holidaywork_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavebalance" VALIDATE CONSTRAINT "dbg_fk_leave_leavebalance_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavebalance" VALIDATE CONSTRAINT "dbg_fk_leave_leavebalance_leave_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" VALIDATE CONSTRAINT "dbg_fk_leave_leavepolicy_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" VALIDATE CONSTRAINT "dbg_fk_leave_leavepolicy_leave_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" VALIDATE CONSTRAINT "dbg_fk_leave_leaverequest_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" VALIDATE CONSTRAINT "dbg_fk_leave_leaverequest_leave_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" VALIDATE CONSTRAINT "dbg_fk_leave_leaverequest_policy_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" VALIDATE CONSTRAINT "dbg_fk_leave_leavetransaction_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" VALIDATE CONSTRAINT "dbg_fk_leave_leavetransaction_leave_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" VALIDATE CONSTRAINT "dbg_fk_leave_leavetransaction_request_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleave" VALIDATE CONSTRAINT "dbg_fk_leave_shortleave_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleaveconversion" VALIDATE CONSTRAINT "dbg_fk_leave_shortleaveconversion_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notificationdelivery" VALIDATE CONSTRAINT "dbg_fk_notifications_notificationdelivery_notification_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplate" VALIDATE CONSTRAINT "dbg_fk_offboarding_clearancetemplate_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplateitem" VALIDATE CONSTRAINT "dbg_fk_offboarding_clearancetemplateitem_template_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_assigned_to_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_exit_workflow_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_source_item_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitinterview" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitinterview_exit_workflow_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitworkflow_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" VALIDATE CONSTRAINT "dbg_fk_offboarding_exitworkflow_resignation_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_finalsettlement" VALIDATE CONSTRAINT "dbg_fk_offboarding_finalsettlement_exit_workflow_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_resignationrequest" VALIDATE CONSTRAINT "dbg_fk_offboarding_resignationrequest_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" VALIDATE CONSTRAINT "dbg_fk_onboarding_employeeletter_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" VALIDATE CONSTRAINT "dbg_fk_onboarding_employeeletter_template_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" VALIDATE CONSTRAINT "dbg_fk_onboarding_employeeonboarding_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" VALIDATE CONSTRAINT "dbg_fk_onboarding_employeeonboarding_template_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingitem_assigned_to_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingitem_document_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingitem_document_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingitem_onboarding_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingitem_source_item_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplate" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingtemplate_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingtemplateitem_document_type_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" VALIDATE CONSTRAINT "dbg_fk_onboarding_onboardingtemplateitem_template_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" VALIDATE CONSTRAINT "dbg_fk_organization_department_head_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" VALIDATE CONSTRAINT "dbg_fk_organization_department_parent_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_designation" VALIDATE CONSTRAINT "dbg_fk_organization_designation_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" VALIDATE CONSTRAINT "dbg_fk_organization_team_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" VALIDATE CONSTRAINT "dbg_fk_organization_team_head_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" VALIDATE CONSTRAINT "dbg_fk_organization_team_parent_team_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeeloan" VALIDATE CONSTRAINT "dbg_fk_payroll_employeeloan_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" VALIDATE CONSTRAINT "dbg_fk_payroll_employeepackage_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" VALIDATE CONSTRAINT "dbg_fk_payroll_employeepackage_supersedes_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_investmentdeclaration" VALIDATE CONSTRAINT "dbg_fk_payroll_investmentdeclaration_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packagedeferral" VALIDATE CONSTRAINT "dbg_fk_payroll_packagedeferral_package_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packagedeferral" VALIDATE CONSTRAINT "dbg_fk_payroll_packagedeferral_released_in_adjustment_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packageperiod" VALIDATE CONSTRAINT "dbg_fk_payroll_packageperiod_package_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" VALIDATE CONSTRAINT "dbg_fk_payroll_payrolladjustment_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" VALIDATE CONSTRAINT "dbg_fk_payroll_payrolladjustment_payroll_run_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrollrun" VALIDATE CONSTRAINT "dbg_fk_payroll_payrollrun_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" VALIDATE CONSTRAINT "dbg_fk_payroll_payslip_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" VALIDATE CONSTRAINT "dbg_fk_payroll_payslip_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" VALIDATE CONSTRAINT "dbg_fk_payroll_payslip_payroll_run_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" VALIDATE CONSTRAINT "dbg_fk_payroll_payslip_salary_structure_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslipline" VALIDATE CONSTRAINT "dbg_fk_payroll_payslipline_component_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslipline" VALIDATE CONSTRAINT "dbg_fk_payroll_payslipline_payslip_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_reimbursementclaim" VALIDATE CONSTRAINT "dbg_fk_payroll_reimbursementclaim_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_reimbursementclaim" VALIDATE CONSTRAINT "dbg_fk_payroll_reimbursementclaim_paid_in_run_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructure" VALIDATE CONSTRAINT "dbg_fk_payroll_salarystructure_employee_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructureline" VALIDATE CONSTRAINT "dbg_fk_payroll_salarystructureline_component_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructureline" VALIDATE CONSTRAINT "dbg_fk_payroll_salarystructureline_salary_structure_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_statutorycontribution" VALIDATE CONSTRAINT "dbg_fk_payroll_statutorycontribution_payslip_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" VALIDATE CONSTRAINT "dbg_fk_recruitment_application_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" VALIDATE CONSTRAINT "dbg_fk_recruitment_application_current_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" VALIDATE CONSTRAINT "dbg_fk_recruitment_application_job_opening_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" VALIDATE CONSTRAINT "dbg_fk_recruitment_applicationevent_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" VALIDATE CONSTRAINT "dbg_fk_recruitment_applicationevent_from_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" VALIDATE CONSTRAINT "dbg_fk_recruitment_applicationevent_to_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidateexternalref" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidateexternalref_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidatenotification_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidatenotification_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidatenotification_job_opening_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidaterejection_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidaterejection_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidaterejection_department_recom_730dbc64";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" VALIDATE CONSTRAINT "dbg_fk_recruitment_candidaterejection_rejection_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_consentrecord" VALIDATE CONSTRAINT "dbg_fk_recruitment_consentrecord_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_consentrecord" VALIDATE CONSTRAINT "dbg_fk_recruitment_consentrecord_origin_batch_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" VALIDATE CONSTRAINT "dbg_fk_recruitment_decisionoverride_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" VALIDATE CONSTRAINT "dbg_fk_recruitment_decisionoverride_new_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" VALIDATE CONSTRAINT "dbg_fk_recruitment_decisionoverride_previous_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" VALIDATE CONSTRAINT "dbg_fk_recruitment_interview_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" VALIDATE CONSTRAINT "dbg_fk_recruitment_interview_interviewer_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" VALIDATE CONSTRAINT "dbg_fk_recruitment_interview_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewfeedback_form_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewfeedback_interview_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewfeedback_submitted_by_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_candidate_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" VALIDATE CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_department_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_designation_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_hiring_manager_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_level_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_location_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_recruiter_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_target_role_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" VALIDATE CONSTRAINT "dbg_fk_recruitment_jobopening_workflow_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" VALIDATE CONSTRAINT "dbg_fk_recruitment_offer_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" VALIDATE CONSTRAINT "dbg_fk_recruitment_offer_designation_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" VALIDATE CONSTRAINT "dbg_fk_recruitment_offer_level_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" VALIDATE CONSTRAINT "dbg_fk_recruitment_offer_reporting_manager_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" VALIDATE CONSTRAINT "dbg_fk_recruitment_stagedecision_application_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" VALIDATE CONSTRAINT "dbg_fk_recruitment_stagedecision_interview_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" VALIDATE CONSTRAINT "dbg_fk_recruitment_stagedecision_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_feedbackfield" VALIDATE CONSTRAINT "dbg_fk_workflows_feedbackfield_form_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_stagetransition" VALIDATE CONSTRAINT "dbg_fk_workflows_stagetransition_from_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_stagetransition" VALIDATE CONSTRAINT "dbg_fk_workflows_stagetransition_to_stage_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" VALIDATE CONSTRAINT "dbg_fk_workflows_workflowstage_feedback_form_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" VALIDATE CONSTRAINT "dbg_fk_workflows_workflowstage_responsible_role_id";',
            reverse_sql='SELECT 1;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" VALIDATE CONSTRAINT "dbg_fk_workflows_workflowstage_workflow_id";',
            reverse_sql='SELECT 1;',
        ),
    ]
