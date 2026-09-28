"""151 composite (fk, organization_id) constraints, added NOT VALID so the lock is brief;
0003 validates them. The existing single-column FKs stay. DEFERRABLE INITIALLY
DEFERRED like every other FK in this schema, so purge's delete-in-any-order
transaction still commits. MATCH SIMPLE: a NULL fk skips the check."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('dbguard', '0001_org_unique_keys'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_rolepermission" ADD CONSTRAINT "dbg_fk_accounts_rolepermission_role_id" FOREIGN KEY ("role_id", "organization_id") REFERENCES "accounts_role" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "accounts_rolepermission" DROP CONSTRAINT IF EXISTS "dbg_fk_accounts_rolepermission_role_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_userrole" ADD CONSTRAINT "dbg_fk_accounts_userrole_role_id" FOREIGN KEY ("role_id", "organization_id") REFERENCES "accounts_role" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "accounts_userrole" DROP CONSTRAINT IF EXISTS "dbg_fk_accounts_userrole_role_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" ADD CONSTRAINT "dbg_fk_assets_asset_category_id" FOREIGN KEY ("category_id", "organization_id") REFERENCES "assets_assetcategory" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "assets_asset" DROP CONSTRAINT IF EXISTS "dbg_fk_assets_asset_category_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" ADD CONSTRAINT "dbg_fk_assets_asset_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "assets_asset" DROP CONSTRAINT IF EXISTS "dbg_fk_assets_asset_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetallocation" ADD CONSTRAINT "dbg_fk_assets_assetallocation_asset_id" FOREIGN KEY ("asset_id", "organization_id") REFERENCES "assets_asset" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "assets_assetallocation" DROP CONSTRAINT IF EXISTS "dbg_fk_assets_assetallocation_asset_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetallocation" ADD CONSTRAINT "dbg_fk_assets_assetallocation_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "assets_assetallocation" DROP CONSTRAINT IF EXISTS "dbg_fk_assets_assetallocation_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetmaintenancelog" ADD CONSTRAINT "dbg_fk_assets_assetmaintenancelog_asset_id" FOREIGN KEY ("asset_id", "organization_id") REFERENCES "assets_asset" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "assets_assetmaintenancelog" DROP CONSTRAINT IF EXISTS "dbg_fk_assets_assetmaintenancelog_asset_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancedevice" ADD CONSTRAINT "dbg_fk_attendance_attendancedevice_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_attendancedevice" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_attendancedevice_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancerecord" ADD CONSTRAINT "dbg_fk_attendance_attendancerecord_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_attendancerecord" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_attendancerecord_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslemployeelink" ADD CONSTRAINT "dbg_fk_attendance_esslemployeelink_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_esslemployeelink" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_esslemployeelink_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslemployeelink" ADD CONSTRAINT "dbg_fk_attendance_esslemployeelink_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_esslemployeelink" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_esslemployeelink_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_rawpunch" ADD CONSTRAINT "dbg_fk_attendance_rawpunch_device_id" FOREIGN KEY ("device_id", "organization_id") REFERENCES "attendance_attendancedevice" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_rawpunch" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_rawpunch_device_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_rawpunch" ADD CONSTRAINT "dbg_fk_attendance_rawpunch_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_rawpunch" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_rawpunch_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_regularizationrequest" ADD CONSTRAINT "dbg_fk_attendance_regularizationrequest_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_regularizationrequest" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_regularizationrequest_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_shiftrule" ADD CONSTRAINT "dbg_fk_attendance_shiftrule_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "attendance_shiftrule" DROP CONSTRAINT IF EXISTS "dbg_fk_attendance_shiftrule_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_emergencycontact" ADD CONSTRAINT "dbg_fk_employees_emergencycontact_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_emergencycontact" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_emergencycontact_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_created_from_candidate_id" FOREIGN KEY ("created_from_candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_created_from_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_designation_id" FOREIGN KEY ("designation_id", "organization_id") REFERENCES "organization_designation" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_designation_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_level_id" FOREIGN KEY ("level_id", "organization_id") REFERENCES "organization_employeelevel" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_level_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_reporting_manager_id" FOREIGN KEY ("reporting_manager_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_reporting_manager_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ADD CONSTRAINT "dbg_fk_employees_employee_team_id" FOREIGN KEY ("team_id", "organization_id") REFERENCES "organization_team" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employee" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employee_team_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeaddress" ADD CONSTRAINT "dbg_fk_employees_employeeaddress_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employeeaddress" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employeeaddress_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" ADD CONSTRAINT "dbg_fk_employees_employeedocument_document_type_id" FOREIGN KEY ("document_type_id", "organization_id") REFERENCES "employees_documenttype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employeedocument" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employeedocument_document_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" ADD CONSTRAINT "dbg_fk_employees_employeedocument_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employeedocument" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employeedocument_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeeducation" ADD CONSTRAINT "dbg_fk_employees_employeeeducation_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employeeeducation" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employeeeducation_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeexperience" ADD CONSTRAINT "dbg_fk_employees_employeeexperience_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_employeeexperience" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_employeeexperience_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" ADD CONSTRAINT "dbg_fk_employees_probationreview_confirmation_letter_id" FOREIGN KEY ("confirmation_letter_id", "organization_id") REFERENCES "onboarding_employeeletter" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_probationreview" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_probationreview_confirmation_letter_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" ADD CONSTRAINT "dbg_fk_employees_probationreview_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_probationreview" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_probationreview_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" ADD CONSTRAINT "dbg_fk_employees_probationreview_reviewer_id" FOREIGN KEY ("reviewer_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "employees_probationreview" DROP CONSTRAINT IF EXISTS "dbg_fk_employees_probationreview_reviewer_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importbatch" ADD CONSTRAINT "dbg_fk_imports_importbatch_job_opening_id" FOREIGN KEY ("job_opening_id", "organization_id") REFERENCES "recruitment_jobopening" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "imports_importbatch" DROP CONSTRAINT IF EXISTS "dbg_fk_imports_importbatch_job_opening_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" ADD CONSTRAINT "dbg_fk_imports_importrow_batch_id" FOREIGN KEY ("batch_id", "organization_id") REFERENCES "imports_importbatch" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "imports_importrow" DROP CONSTRAINT IF EXISTS "dbg_fk_imports_importrow_batch_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" ADD CONSTRAINT "dbg_fk_imports_importrow_created_employee_id" FOREIGN KEY ("created_employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "imports_importrow" DROP CONSTRAINT IF EXISTS "dbg_fk_imports_importrow_created_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" ADD CONSTRAINT "dbg_fk_imports_importrow_matched_candidate_id" FOREIGN KEY ("matched_candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "imports_importrow" DROP CONSTRAINT IF EXISTS "dbg_fk_imports_importrow_matched_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "itaccounts_companyemailaccount" ADD CONSTRAINT "dbg_fk_itaccounts_companyemailaccount_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "itaccounts_companyemailaccount" DROP CONSTRAINT IF EXISTS "dbg_fk_itaccounts_companyemailaccount_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holiday" ADD CONSTRAINT "dbg_fk_leave_holiday_calendar_id" FOREIGN KEY ("calendar_id", "organization_id") REFERENCES "leave_holidaycalendar" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_holiday" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_holiday_calendar_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaycalendar" ADD CONSTRAINT "dbg_fk_leave_holidaycalendar_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_holidaycalendar" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_holidaycalendar_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaywork" ADD CONSTRAINT "dbg_fk_leave_holidaywork_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_holidaywork" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_holidaywork_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavebalance" ADD CONSTRAINT "dbg_fk_leave_leavebalance_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavebalance" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavebalance_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavebalance" ADD CONSTRAINT "dbg_fk_leave_leavebalance_leave_type_id" FOREIGN KEY ("leave_type_id", "organization_id") REFERENCES "leave_leavetype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavebalance" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavebalance_leave_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" ADD CONSTRAINT "dbg_fk_leave_leavepolicy_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavepolicy" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavepolicy_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" ADD CONSTRAINT "dbg_fk_leave_leavepolicy_leave_type_id" FOREIGN KEY ("leave_type_id", "organization_id") REFERENCES "leave_leavetype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavepolicy" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavepolicy_leave_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" ADD CONSTRAINT "dbg_fk_leave_leaverequest_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leaverequest" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leaverequest_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" ADD CONSTRAINT "dbg_fk_leave_leaverequest_leave_type_id" FOREIGN KEY ("leave_type_id", "organization_id") REFERENCES "leave_leavetype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leaverequest" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leaverequest_leave_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" ADD CONSTRAINT "dbg_fk_leave_leaverequest_policy_id" FOREIGN KEY ("policy_id", "organization_id") REFERENCES "leave_leavepolicy" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leaverequest" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leaverequest_policy_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" ADD CONSTRAINT "dbg_fk_leave_leavetransaction_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavetransaction" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavetransaction_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" ADD CONSTRAINT "dbg_fk_leave_leavetransaction_leave_type_id" FOREIGN KEY ("leave_type_id", "organization_id") REFERENCES "leave_leavetype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavetransaction" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavetransaction_leave_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" ADD CONSTRAINT "dbg_fk_leave_leavetransaction_request_id" FOREIGN KEY ("request_id", "organization_id") REFERENCES "leave_leaverequest" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_leavetransaction" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_leavetransaction_request_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleave" ADD CONSTRAINT "dbg_fk_leave_shortleave_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_shortleave" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_shortleave_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleaveconversion" ADD CONSTRAINT "dbg_fk_leave_shortleaveconversion_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "leave_shortleaveconversion" DROP CONSTRAINT IF EXISTS "dbg_fk_leave_shortleaveconversion_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notificationdelivery" ADD CONSTRAINT "dbg_fk_notifications_notificationdelivery_notification_id" FOREIGN KEY ("notification_id", "organization_id") REFERENCES "notifications_notification" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "notifications_notificationdelivery" DROP CONSTRAINT IF EXISTS "dbg_fk_notifications_notificationdelivery_notification_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplate" ADD CONSTRAINT "dbg_fk_offboarding_clearancetemplate_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_clearancetemplate" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_clearancetemplate_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplateitem" ADD CONSTRAINT "dbg_fk_offboarding_clearancetemplateitem_template_id" FOREIGN KEY ("template_id", "organization_id") REFERENCES "offboarding_clearancetemplate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_clearancetemplateitem" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_clearancetemplateitem_template_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" ADD CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_assigned_to_id" FOREIGN KEY ("assigned_to_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitclearanceitem" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitclearanceitem_assigned_to_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" ADD CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_exit_workflow_id" FOREIGN KEY ("exit_workflow_id", "organization_id") REFERENCES "offboarding_exitworkflow" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitclearanceitem" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitclearanceitem_exit_workflow_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" ADD CONSTRAINT "dbg_fk_offboarding_exitclearanceitem_source_item_id" FOREIGN KEY ("source_item_id", "organization_id") REFERENCES "offboarding_clearancetemplateitem" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitclearanceitem" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitclearanceitem_source_item_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitinterview" ADD CONSTRAINT "dbg_fk_offboarding_exitinterview_exit_workflow_id" FOREIGN KEY ("exit_workflow_id", "organization_id") REFERENCES "offboarding_exitworkflow" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitinterview" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitinterview_exit_workflow_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" ADD CONSTRAINT "dbg_fk_offboarding_exitworkflow_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitworkflow" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitworkflow_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" ADD CONSTRAINT "dbg_fk_offboarding_exitworkflow_resignation_id" FOREIGN KEY ("resignation_id", "organization_id") REFERENCES "offboarding_resignationrequest" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_exitworkflow" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_exitworkflow_resignation_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_finalsettlement" ADD CONSTRAINT "dbg_fk_offboarding_finalsettlement_exit_workflow_id" FOREIGN KEY ("exit_workflow_id", "organization_id") REFERENCES "offboarding_exitworkflow" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_finalsettlement" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_finalsettlement_exit_workflow_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_resignationrequest" ADD CONSTRAINT "dbg_fk_offboarding_resignationrequest_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "offboarding_resignationrequest" DROP CONSTRAINT IF EXISTS "dbg_fk_offboarding_resignationrequest_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" ADD CONSTRAINT "dbg_fk_onboarding_employeeletter_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_employeeletter" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_employeeletter_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" ADD CONSTRAINT "dbg_fk_onboarding_employeeletter_template_id" FOREIGN KEY ("template_id", "organization_id") REFERENCES "onboarding_lettertemplate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_employeeletter" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_employeeletter_template_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" ADD CONSTRAINT "dbg_fk_onboarding_employeeonboarding_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_employeeonboarding" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_employeeonboarding_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" ADD CONSTRAINT "dbg_fk_onboarding_employeeonboarding_template_id" FOREIGN KEY ("template_id", "organization_id") REFERENCES "onboarding_onboardingtemplate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_employeeonboarding" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_employeeonboarding_template_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingitem_assigned_to_id" FOREIGN KEY ("assigned_to_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingitem_assigned_to_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingitem_document_id" FOREIGN KEY ("document_id", "organization_id") REFERENCES "employees_employeedocument" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingitem_document_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingitem_document_type_id" FOREIGN KEY ("document_type_id", "organization_id") REFERENCES "employees_documenttype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingitem_document_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingitem_onboarding_id" FOREIGN KEY ("onboarding_id", "organization_id") REFERENCES "onboarding_employeeonboarding" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingitem_onboarding_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingitem_source_item_id" FOREIGN KEY ("source_item_id", "organization_id") REFERENCES "onboarding_onboardingtemplateitem" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingitem_source_item_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplate" ADD CONSTRAINT "dbg_fk_onboarding_onboardingtemplate_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingtemplate" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingtemplate_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingtemplateitem_document_type_id" FOREIGN KEY ("document_type_id", "organization_id") REFERENCES "employees_documenttype" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingtemplateitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingtemplateitem_document_type_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" ADD CONSTRAINT "dbg_fk_onboarding_onboardingtemplateitem_template_id" FOREIGN KEY ("template_id", "organization_id") REFERENCES "onboarding_onboardingtemplate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "onboarding_onboardingtemplateitem" DROP CONSTRAINT IF EXISTS "dbg_fk_onboarding_onboardingtemplateitem_template_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" ADD CONSTRAINT "dbg_fk_organization_department_head_employee_id" FOREIGN KEY ("head_employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_department" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_department_head_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" ADD CONSTRAINT "dbg_fk_organization_department_parent_department_id" FOREIGN KEY ("parent_department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_department" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_department_parent_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_designation" ADD CONSTRAINT "dbg_fk_organization_designation_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_designation" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_designation_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" ADD CONSTRAINT "dbg_fk_organization_team_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_team" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_team_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" ADD CONSTRAINT "dbg_fk_organization_team_head_employee_id" FOREIGN KEY ("head_employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_team" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_team_head_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" ADD CONSTRAINT "dbg_fk_organization_team_parent_team_id" FOREIGN KEY ("parent_team_id", "organization_id") REFERENCES "organization_team" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "organization_team" DROP CONSTRAINT IF EXISTS "dbg_fk_organization_team_parent_team_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeeloan" ADD CONSTRAINT "dbg_fk_payroll_employeeloan_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_employeeloan" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_employeeloan_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" ADD CONSTRAINT "dbg_fk_payroll_employeepackage_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_employeepackage" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_employeepackage_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" ADD CONSTRAINT "dbg_fk_payroll_employeepackage_supersedes_id" FOREIGN KEY ("supersedes_id", "organization_id") REFERENCES "payroll_employeepackage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_employeepackage" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_employeepackage_supersedes_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_investmentdeclaration" ADD CONSTRAINT "dbg_fk_payroll_investmentdeclaration_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_investmentdeclaration" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_investmentdeclaration_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packagedeferral" ADD CONSTRAINT "dbg_fk_payroll_packagedeferral_package_id" FOREIGN KEY ("package_id", "organization_id") REFERENCES "payroll_employeepackage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_packagedeferral" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_packagedeferral_package_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packagedeferral" ADD CONSTRAINT "dbg_fk_payroll_packagedeferral_released_in_adjustment_id" FOREIGN KEY ("released_in_adjustment_id", "organization_id") REFERENCES "payroll_payrolladjustment" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_packagedeferral" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_packagedeferral_released_in_adjustment_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packageperiod" ADD CONSTRAINT "dbg_fk_payroll_packageperiod_package_id" FOREIGN KEY ("package_id", "organization_id") REFERENCES "payroll_employeepackage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_packageperiod" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_packageperiod_package_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" ADD CONSTRAINT "dbg_fk_payroll_payrolladjustment_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payrolladjustment" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payrolladjustment_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" ADD CONSTRAINT "dbg_fk_payroll_payrolladjustment_payroll_run_id" FOREIGN KEY ("payroll_run_id", "organization_id") REFERENCES "payroll_payrollrun" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payrolladjustment" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payrolladjustment_payroll_run_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrollrun" ADD CONSTRAINT "dbg_fk_payroll_payrollrun_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payrollrun" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payrollrun_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ADD CONSTRAINT "dbg_fk_payroll_payslip_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslip" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslip_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ADD CONSTRAINT "dbg_fk_payroll_payslip_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslip" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslip_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ADD CONSTRAINT "dbg_fk_payroll_payslip_payroll_run_id" FOREIGN KEY ("payroll_run_id", "organization_id") REFERENCES "payroll_payrollrun" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslip" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslip_payroll_run_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ADD CONSTRAINT "dbg_fk_payroll_payslip_salary_structure_id" FOREIGN KEY ("salary_structure_id", "organization_id") REFERENCES "payroll_salarystructure" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslip" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslip_salary_structure_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslipline" ADD CONSTRAINT "dbg_fk_payroll_payslipline_component_id" FOREIGN KEY ("component_id", "organization_id") REFERENCES "payroll_salarycomponent" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslipline" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslipline_component_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslipline" ADD CONSTRAINT "dbg_fk_payroll_payslipline_payslip_id" FOREIGN KEY ("payslip_id", "organization_id") REFERENCES "payroll_payslip" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_payslipline" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_payslipline_payslip_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_reimbursementclaim" ADD CONSTRAINT "dbg_fk_payroll_reimbursementclaim_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_reimbursementclaim" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_reimbursementclaim_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_reimbursementclaim" ADD CONSTRAINT "dbg_fk_payroll_reimbursementclaim_paid_in_run_id" FOREIGN KEY ("paid_in_run_id", "organization_id") REFERENCES "payroll_payrollrun" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_reimbursementclaim" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_reimbursementclaim_paid_in_run_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructure" ADD CONSTRAINT "dbg_fk_payroll_salarystructure_employee_id" FOREIGN KEY ("employee_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_salarystructure" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_salarystructure_employee_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructureline" ADD CONSTRAINT "dbg_fk_payroll_salarystructureline_component_id" FOREIGN KEY ("component_id", "organization_id") REFERENCES "payroll_salarycomponent" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_salarystructureline" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_salarystructureline_component_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructureline" ADD CONSTRAINT "dbg_fk_payroll_salarystructureline_salary_structure_id" FOREIGN KEY ("salary_structure_id", "organization_id") REFERENCES "payroll_salarystructure" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_salarystructureline" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_salarystructureline_salary_structure_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_statutorycontribution" ADD CONSTRAINT "dbg_fk_payroll_statutorycontribution_payslip_id" FOREIGN KEY ("payslip_id", "organization_id") REFERENCES "payroll_payslip" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "payroll_statutorycontribution" DROP CONSTRAINT IF EXISTS "dbg_fk_payroll_statutorycontribution_payslip_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" ADD CONSTRAINT "dbg_fk_recruitment_application_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_application" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_application_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" ADD CONSTRAINT "dbg_fk_recruitment_application_current_stage_id" FOREIGN KEY ("current_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_application" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_application_current_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" ADD CONSTRAINT "dbg_fk_recruitment_application_job_opening_id" FOREIGN KEY ("job_opening_id", "organization_id") REFERENCES "recruitment_jobopening" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_application" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_application_job_opening_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" ADD CONSTRAINT "dbg_fk_recruitment_applicationevent_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_applicationevent" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_applicationevent_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" ADD CONSTRAINT "dbg_fk_recruitment_applicationevent_from_stage_id" FOREIGN KEY ("from_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_applicationevent" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_applicationevent_from_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" ADD CONSTRAINT "dbg_fk_recruitment_applicationevent_to_stage_id" FOREIGN KEY ("to_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_applicationevent" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_applicationevent_to_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidateexternalref" ADD CONSTRAINT "dbg_fk_recruitment_candidateexternalref_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidateexternalref" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidateexternalref_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" ADD CONSTRAINT "dbg_fk_recruitment_candidatenotification_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidatenotification" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidatenotification_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" ADD CONSTRAINT "dbg_fk_recruitment_candidatenotification_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidatenotification" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidatenotification_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" ADD CONSTRAINT "dbg_fk_recruitment_candidatenotification_job_opening_id" FOREIGN KEY ("job_opening_id", "organization_id") REFERENCES "recruitment_jobopening" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidatenotification" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidatenotification_job_opening_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" ADD CONSTRAINT "dbg_fk_recruitment_candidaterejection_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidaterejection" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidaterejection_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" ADD CONSTRAINT "dbg_fk_recruitment_candidaterejection_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidaterejection" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidaterejection_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" ADD CONSTRAINT "dbg_fk_recruitment_candidaterejection_department_recom_730dbc64" FOREIGN KEY ("department_recommendation_id", "organization_id") REFERENCES "recruitment_stagedecision" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidaterejection" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidaterejection_department_recom_730dbc64";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" ADD CONSTRAINT "dbg_fk_recruitment_candidaterejection_rejection_stage_id" FOREIGN KEY ("rejection_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_candidaterejection" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_candidaterejection_rejection_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_consentrecord" ADD CONSTRAINT "dbg_fk_recruitment_consentrecord_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_consentrecord" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_consentrecord_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_consentrecord" ADD CONSTRAINT "dbg_fk_recruitment_consentrecord_origin_batch_id" FOREIGN KEY ("origin_batch_id", "organization_id") REFERENCES "imports_importbatch" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_consentrecord" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_consentrecord_origin_batch_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" ADD CONSTRAINT "dbg_fk_recruitment_decisionoverride_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_decisionoverride" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_decisionoverride_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" ADD CONSTRAINT "dbg_fk_recruitment_decisionoverride_new_stage_id" FOREIGN KEY ("new_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_decisionoverride" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_decisionoverride_new_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" ADD CONSTRAINT "dbg_fk_recruitment_decisionoverride_previous_stage_id" FOREIGN KEY ("previous_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_decisionoverride" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_decisionoverride_previous_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" ADD CONSTRAINT "dbg_fk_recruitment_interview_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interview" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interview_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" ADD CONSTRAINT "dbg_fk_recruitment_interview_interviewer_id" FOREIGN KEY ("interviewer_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interview" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interview_interviewer_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" ADD CONSTRAINT "dbg_fk_recruitment_interview_stage_id" FOREIGN KEY ("stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interview" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interview_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" ADD CONSTRAINT "dbg_fk_recruitment_interviewfeedback_form_id" FOREIGN KEY ("form_id", "organization_id") REFERENCES "workflows_feedbackform" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewfeedback" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewfeedback_form_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" ADD CONSTRAINT "dbg_fk_recruitment_interviewfeedback_interview_id" FOREIGN KEY ("interview_id", "organization_id") REFERENCES "recruitment_interview" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewfeedback" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewfeedback_interview_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" ADD CONSTRAINT "dbg_fk_recruitment_interviewfeedback_submitted_by_id" FOREIGN KEY ("submitted_by_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewfeedback" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewfeedback_submitted_by_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" ADD CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewslotinvite" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewslotinvite_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" ADD CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_candidate_id" FOREIGN KEY ("candidate_id", "organization_id") REFERENCES "recruitment_candidate" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewslotinvite" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewslotinvite_candidate_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" ADD CONSTRAINT "dbg_fk_recruitment_interviewslotinvite_stage_id" FOREIGN KEY ("stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_interviewslotinvite" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_interviewslotinvite_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_department_id" FOREIGN KEY ("department_id", "organization_id") REFERENCES "organization_department" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_department_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_designation_id" FOREIGN KEY ("designation_id", "organization_id") REFERENCES "organization_designation" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_designation_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_hiring_manager_id" FOREIGN KEY ("hiring_manager_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_hiring_manager_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_level_id" FOREIGN KEY ("level_id", "organization_id") REFERENCES "organization_employeelevel" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_level_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_location_id" FOREIGN KEY ("location_id", "organization_id") REFERENCES "organization_location" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_location_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_recruiter_id" FOREIGN KEY ("recruiter_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_recruiter_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_target_role_id" FOREIGN KEY ("target_role_id", "organization_id") REFERENCES "accounts_role" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_target_role_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ADD CONSTRAINT "dbg_fk_recruitment_jobopening_workflow_id" FOREIGN KEY ("workflow_id", "organization_id") REFERENCES "workflows_hiringworkflow" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_jobopening" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_jobopening_workflow_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" ADD CONSTRAINT "dbg_fk_recruitment_offer_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_offer" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_offer_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" ADD CONSTRAINT "dbg_fk_recruitment_offer_designation_id" FOREIGN KEY ("designation_id", "organization_id") REFERENCES "organization_designation" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_offer" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_offer_designation_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" ADD CONSTRAINT "dbg_fk_recruitment_offer_level_id" FOREIGN KEY ("level_id", "organization_id") REFERENCES "organization_employeelevel" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_offer" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_offer_level_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" ADD CONSTRAINT "dbg_fk_recruitment_offer_reporting_manager_id" FOREIGN KEY ("reporting_manager_id", "organization_id") REFERENCES "employees_employee" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_offer" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_offer_reporting_manager_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" ADD CONSTRAINT "dbg_fk_recruitment_stagedecision_application_id" FOREIGN KEY ("application_id", "organization_id") REFERENCES "recruitment_application" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_stagedecision" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_stagedecision_application_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" ADD CONSTRAINT "dbg_fk_recruitment_stagedecision_interview_id" FOREIGN KEY ("interview_id", "organization_id") REFERENCES "recruitment_interview" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_stagedecision" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_stagedecision_interview_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" ADD CONSTRAINT "dbg_fk_recruitment_stagedecision_stage_id" FOREIGN KEY ("stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "recruitment_stagedecision" DROP CONSTRAINT IF EXISTS "dbg_fk_recruitment_stagedecision_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_feedbackfield" ADD CONSTRAINT "dbg_fk_workflows_feedbackfield_form_id" FOREIGN KEY ("form_id", "organization_id") REFERENCES "workflows_feedbackform" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_feedbackfield" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_feedbackfield_form_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_stagetransition" ADD CONSTRAINT "dbg_fk_workflows_stagetransition_from_stage_id" FOREIGN KEY ("from_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_stagetransition" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_stagetransition_from_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_stagetransition" ADD CONSTRAINT "dbg_fk_workflows_stagetransition_to_stage_id" FOREIGN KEY ("to_stage_id", "organization_id") REFERENCES "workflows_workflowstage" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_stagetransition" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_stagetransition_to_stage_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" ADD CONSTRAINT "dbg_fk_workflows_workflowstage_feedback_form_id" FOREIGN KEY ("feedback_form_id", "organization_id") REFERENCES "workflows_feedbackform" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_workflowstage" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_workflowstage_feedback_form_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" ADD CONSTRAINT "dbg_fk_workflows_workflowstage_responsible_role_id" FOREIGN KEY ("responsible_role_id", "organization_id") REFERENCES "accounts_role" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_workflowstage" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_workflowstage_responsible_role_id";',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" ADD CONSTRAINT "dbg_fk_workflows_workflowstage_workflow_id" FOREIGN KEY ("workflow_id", "organization_id") REFERENCES "workflows_hiringworkflow" ("id", "organization_id") DEFERRABLE INITIALLY DEFERRED NOT VALID;',
            reverse_sql='ALTER TABLE "workflows_workflowstage" DROP CONSTRAINT IF EXISTS "dbg_fk_workflows_workflowstage_workflow_id";',
        ),
    ]
