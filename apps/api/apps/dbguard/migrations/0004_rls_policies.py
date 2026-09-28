"""Row-level security, RELEASE 1: policies on 97 tenant tables, the membership
table, the audit table and subscriptions. ENABLE, not FORCE: the owner (which
runs migrations, and which the app still connects as in release 1) is exempt,
so nothing changes behaviour until the runtime switches to generic_hrms_app
(release 2). Unset app.org_id => NULL => every tenant policy matches no rows."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('dbguard', '0003_validate_org_fks'),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE OR REPLACE FUNCTION dbguard_current_org() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$;\nCREATE OR REPLACE FUNCTION dbguard_current_user() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;",
            reverse_sql='DROP FUNCTION IF EXISTS dbguard_current_org(); DROP FUNCTION IF EXISTS dbguard_current_user();',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_role" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "accounts_role" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "accounts_role"; ALTER TABLE "accounts_role" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_rolepermission" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "accounts_rolepermission" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "accounts_rolepermission"; ALTER TABLE "accounts_rolepermission" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_userpermissionoverride" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "accounts_userpermissionoverride" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "accounts_userpermissionoverride"; ALTER TABLE "accounts_userpermissionoverride" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "accounts_userrole" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "accounts_userrole" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "accounts_userrole"; ALTER TABLE "accounts_userrole" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_asset" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "assets_asset" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "assets_asset"; ALTER TABLE "assets_asset" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetallocation" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "assets_assetallocation" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "assets_assetallocation"; ALTER TABLE "assets_assetallocation" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetcategory" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "assets_assetcategory" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "assets_assetcategory"; ALTER TABLE "assets_assetcategory" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "assets_assetmaintenancelog" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "assets_assetmaintenancelog" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "assets_assetmaintenancelog"; ALTER TABLE "assets_assetmaintenancelog" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancedevice" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_attendancedevice" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_attendancedevice"; ALTER TABLE "attendance_attendancedevice" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_attendancerecord" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_attendancerecord" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_attendancerecord"; ALTER TABLE "attendance_attendancerecord" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslemployeelink" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_esslemployeelink" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_esslemployeelink"; ALTER TABLE "attendance_esslemployeelink" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_esslsyncrun" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_esslsyncrun" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_esslsyncrun"; ALTER TABLE "attendance_esslsyncrun" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_orgattendanceintegration" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_orgattendanceintegration" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_orgattendanceintegration"; ALTER TABLE "attendance_orgattendanceintegration" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_rawpunch" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_rawpunch" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_rawpunch"; ALTER TABLE "attendance_rawpunch" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_regularizationrequest" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_regularizationrequest" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_regularizationrequest"; ALTER TABLE "attendance_regularizationrequest" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "attendance_shiftrule" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "attendance_shiftrule" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "attendance_shiftrule"; ALTER TABLE "attendance_shiftrule" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_documenttype" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_documenttype" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_documenttype"; ALTER TABLE "employees_documenttype" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_emergencycontact" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_emergencycontact" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_emergencycontact"; ALTER TABLE "employees_emergencycontact" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employee" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_employee" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_employee"; ALTER TABLE "employees_employee" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeaddress" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_employeeaddress" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_employeeaddress"; ALTER TABLE "employees_employeeaddress" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeedocument" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_employeedocument" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_employeedocument"; ALTER TABLE "employees_employeedocument" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeeducation" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_employeeeducation" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_employeeeducation"; ALTER TABLE "employees_employeeeducation" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_employeeexperience" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_employeeexperience" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_employeeexperience"; ALTER TABLE "employees_employeeexperience" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "employees_probationreview" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "employees_probationreview" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "employees_probationreview"; ALTER TABLE "employees_probationreview" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importbatch" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "imports_importbatch" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "imports_importbatch"; ALTER TABLE "imports_importbatch" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "imports_importrow" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "imports_importrow" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "imports_importrow"; ALTER TABLE "imports_importrow" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "itaccounts_companyemailaccount" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "itaccounts_companyemailaccount" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "itaccounts_companyemailaccount"; ALTER TABLE "itaccounts_companyemailaccount" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holiday" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_holiday" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_holiday"; ALTER TABLE "leave_holiday" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaycalendar" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_holidaycalendar" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_holidaycalendar"; ALTER TABLE "leave_holidaycalendar" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_holidaywork" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_holidaywork" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_holidaywork"; ALTER TABLE "leave_holidaywork" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavebalance" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leavebalance" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leavebalance"; ALTER TABLE "leave_leavebalance" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavepolicy" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leavepolicy" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leavepolicy"; ALTER TABLE "leave_leavepolicy" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leaverequest" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leaverequest" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leaverequest"; ALTER TABLE "leave_leaverequest" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavesettings" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leavesettings" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leavesettings"; ALTER TABLE "leave_leavesettings" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetransaction" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leavetransaction" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leavetransaction"; ALTER TABLE "leave_leavetransaction" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_leavetype" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_leavetype" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_leavetype"; ALTER TABLE "leave_leavetype" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleave" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_shortleave" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_shortleave"; ALTER TABLE "leave_shortleave" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "leave_shortleaveconversion" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "leave_shortleaveconversion" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "leave_shortleaveconversion"; ALTER TABLE "leave_shortleaveconversion" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notification" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "notifications_notification" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "notifications_notification"; ALTER TABLE "notifications_notification" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notificationdelivery" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "notifications_notificationdelivery" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "notifications_notificationdelivery"; ALTER TABLE "notifications_notificationdelivery" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "notifications_notificationpreference" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "notifications_notificationpreference" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "notifications_notificationpreference"; ALTER TABLE "notifications_notificationpreference" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplate" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_clearancetemplate" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_clearancetemplate"; ALTER TABLE "offboarding_clearancetemplate" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_clearancetemplateitem" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_clearancetemplateitem" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_clearancetemplateitem"; ALTER TABLE "offboarding_clearancetemplateitem" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitclearanceitem" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_exitclearanceitem" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_exitclearanceitem"; ALTER TABLE "offboarding_exitclearanceitem" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitinterview" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_exitinterview" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_exitinterview"; ALTER TABLE "offboarding_exitinterview" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_exitworkflow" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_exitworkflow" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_exitworkflow"; ALTER TABLE "offboarding_exitworkflow" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_finalsettlement" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_finalsettlement" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_finalsettlement"; ALTER TABLE "offboarding_finalsettlement" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "offboarding_resignationrequest" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "offboarding_resignationrequest" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "offboarding_resignationrequest"; ALTER TABLE "offboarding_resignationrequest" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeletter" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_employeeletter" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_employeeletter"; ALTER TABLE "onboarding_employeeletter" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_employeeonboarding" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_employeeonboarding" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_employeeonboarding"; ALTER TABLE "onboarding_employeeonboarding" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_lettertemplate" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_lettertemplate" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_lettertemplate"; ALTER TABLE "onboarding_lettertemplate" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingitem" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_onboardingitem" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_onboardingitem"; ALTER TABLE "onboarding_onboardingitem" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplate" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_onboardingtemplate" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_onboardingtemplate"; ALTER TABLE "onboarding_onboardingtemplate" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "onboarding_onboardingtemplateitem" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "onboarding_onboardingtemplateitem" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "onboarding_onboardingtemplateitem"; ALTER TABLE "onboarding_onboardingtemplateitem" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_department" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_department" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_department"; ALTER TABLE "organization_department" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_designation" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_designation" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_designation"; ALTER TABLE "organization_designation" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_employeelevel" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_employeelevel" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_employeelevel"; ALTER TABLE "organization_employeelevel" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_location" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_location" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_location"; ALTER TABLE "organization_location" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_orgemailtemplate" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_orgemailtemplate" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_orgemailtemplate"; ALTER TABLE "organization_orgemailtemplate" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_team" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_team" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_team"; ALTER TABLE "organization_team" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeeloan" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_employeeloan" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_employeeloan"; ALTER TABLE "payroll_employeeloan" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_employeepackage" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_employeepackage" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_employeepackage"; ALTER TABLE "payroll_employeepackage" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_investmentdeclaration" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_investmentdeclaration" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_investmentdeclaration"; ALTER TABLE "payroll_investmentdeclaration" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packagedeferral" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_packagedeferral" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_packagedeferral"; ALTER TABLE "payroll_packagedeferral" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_packageperiod" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_packageperiod" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_packageperiod"; ALTER TABLE "payroll_packageperiod" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrolladjustment" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_payrolladjustment" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_payrolladjustment"; ALTER TABLE "payroll_payrolladjustment" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payrollrun" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_payrollrun" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_payrollrun"; ALTER TABLE "payroll_payrollrun" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslip" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_payslip" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_payslip"; ALTER TABLE "payroll_payslip" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_payslipline" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_payslipline" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_payslipline"; ALTER TABLE "payroll_payslipline" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_reimbursementclaim" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_reimbursementclaim" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_reimbursementclaim"; ALTER TABLE "payroll_reimbursementclaim" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarycomponent" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_salarycomponent" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_salarycomponent"; ALTER TABLE "payroll_salarycomponent" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructure" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_salarystructure" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_salarystructure"; ALTER TABLE "payroll_salarystructure" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_salarystructureline" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_salarystructureline" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_salarystructureline"; ALTER TABLE "payroll_salarystructureline" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "payroll_statutorycontribution" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "payroll_statutorycontribution" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "payroll_statutorycontribution"; ALTER TABLE "payroll_statutorycontribution" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "platform_supportgrant" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "platform_supportgrant" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "platform_supportgrant"; ALTER TABLE "platform_supportgrant" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_application" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_application" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_application"; ALTER TABLE "recruitment_application" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_applicationevent" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_applicationevent" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_applicationevent"; ALTER TABLE "recruitment_applicationevent" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidate" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_candidate" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_candidate"; ALTER TABLE "recruitment_candidate" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidateexternalref" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_candidateexternalref" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_candidateexternalref"; ALTER TABLE "recruitment_candidateexternalref" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidatenotification" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_candidatenotification" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_candidatenotification"; ALTER TABLE "recruitment_candidatenotification" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_candidaterejection" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_candidaterejection" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_candidaterejection"; ALTER TABLE "recruitment_candidaterejection" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_consentrecord" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_consentrecord" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_consentrecord"; ALTER TABLE "recruitment_consentrecord" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_decisionoverride" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_decisionoverride" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_decisionoverride"; ALTER TABLE "recruitment_decisionoverride" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interview" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_interview" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_interview"; ALTER TABLE "recruitment_interview" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewfeedback" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_interviewfeedback" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_interviewfeedback"; ALTER TABLE "recruitment_interviewfeedback" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_interviewslotinvite" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_interviewslotinvite" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_interviewslotinvite"; ALTER TABLE "recruitment_interviewslotinvite" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_jobopening" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_jobopening" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_jobopening"; ALTER TABLE "recruitment_jobopening" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_offer" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_offer" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_offer"; ALTER TABLE "recruitment_offer" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "recruitment_stagedecision" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "recruitment_stagedecision" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "recruitment_stagedecision"; ALTER TABLE "recruitment_stagedecision" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "reporting_metricsnapshot" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "reporting_metricsnapshot" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "reporting_metricsnapshot"; ALTER TABLE "reporting_metricsnapshot" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_feedbackfield" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "workflows_feedbackfield" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "workflows_feedbackfield"; ALTER TABLE "workflows_feedbackfield" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_feedbackform" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "workflows_feedbackform" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "workflows_feedbackform"; ALTER TABLE "workflows_feedbackform" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_hiringworkflow" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "workflows_hiringworkflow" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "workflows_hiringworkflow"; ALTER TABLE "workflows_hiringworkflow" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_stagetransition" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "workflows_stagetransition" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "workflows_stagetransition"; ALTER TABLE "workflows_stagetransition" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "workflows_workflowstage" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "workflows_workflowstage" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "workflows_workflowstage"; ALTER TABLE "workflows_workflowstage" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_orgsettings" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_orgsettings" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_orgsettings"; ALTER TABLE "organization_orgsettings" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_orgemailconfig" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON "organization_orgemailconfig" FOR ALL USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS tenant_isolation ON "organization_orgemailconfig"; ALTER TABLE "organization_orgemailconfig" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "organization_organizationmembership" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY membership_isolation ON "organization_organizationmembership" FOR ALL USING (organization_id = dbguard_current_org() OR user_id = dbguard_current_user()) WITH CHECK (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS membership_isolation ON "organization_organizationmembership"; ALTER TABLE "organization_organizationmembership" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "audit_auditlog" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY audit_read ON "audit_auditlog" FOR SELECT USING (organization_id = dbguard_current_org());\nCREATE POLICY audit_scrub ON "audit_auditlog" FOR UPDATE USING (organization_id = dbguard_current_org()) WITH CHECK (organization_id = dbguard_current_org());\nCREATE POLICY audit_append ON "audit_auditlog" FOR INSERT WITH CHECK (organization_id IS NULL OR organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS audit_read ON "audit_auditlog"; DROP POLICY IF EXISTS audit_scrub ON "audit_auditlog"; DROP POLICY IF EXISTS audit_append ON "audit_auditlog"; ALTER TABLE "audit_auditlog" DISABLE ROW LEVEL SECURITY;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "platform_subscription" ENABLE ROW LEVEL SECURITY;\nCREATE POLICY subscription_read ON "platform_subscription" FOR SELECT USING (organization_id = dbguard_current_org());',
            reverse_sql='DROP POLICY IF EXISTS subscription_read ON "platform_subscription"; ALTER TABLE "platform_subscription" DISABLE ROW LEVEL SECURITY;',
        ),
    ]
