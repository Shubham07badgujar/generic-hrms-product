/**
 * Routes.
 *
 * Route-level guards mirror the API's own gates: `RequirePermission` on a route
 * asks the same (resource, action) question the endpoint behind it will ask.
 * When they disagree the API wins — the guard exists so the user gets an
 * explanation instead of a failed request, not so the request is trusted.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './AppShell'
import { usePermissions } from './AuthProvider'
import { PlatformRoutes } from './platformRoutes'
import { RequireAnonymous, RequireAuth, RequirePermission } from './guards'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ChangePasswordPage } from '@/features/auth/ChangePasswordPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { PipelinePage } from '@/features/recruitment/PipelinePage'
import { JobDetailPage, JobsPage } from '@/features/recruitment/JobsPage'
import { CandidateProfilePage, CandidatesPage } from '@/features/recruitment/CandidatesPage'
import { ImportCandidatesPage } from '@/features/recruitment/ImportCandidatesPage'
import { ApplyPage } from '@/features/recruitment/ApplyPage'
import { SlotPage } from '@/features/recruitment/SlotPage'
import { ApplicationDetailPage } from '@/features/recruitment/ApplicationDetailPage'
import { InterviewsPage } from '@/features/recruitment/InterviewsPage'
import { DecisionQueuePage } from '@/features/recruitment/DecisionQueuePage'
import { OffersPage } from '@/features/recruitment/OffersPage'
import { WorkflowsPage } from '@/features/recruitment/WorkflowsPage'
import { EmployeesPage } from '@/features/employees/EmployeesPage'
import {
  EmployeeDetailPage,
  MyProfilePage,
} from '@/features/employees/EmployeeProfilePage'
import { AttendancePage } from '@/features/attendance/AttendancePage'
import { EmployeeMonthPage } from '@/features/attendance/EmployeeMonthPage'
import { EsslIntegrationPage } from '@/features/attendance/EsslIntegrationPage'
import { DocumentsPage } from '@/features/employees/DocumentsPage'
import { HandbookPage } from '@/features/employees/HandbookPage'
import { OnboardingDashboard } from '@/features/employees/OnboardingDashboard'
import { LeavePage } from '@/features/leave/LeavePage'
import { OffboardingPage } from '@/features/offboarding/OffboardingPage'
import { ExitDetailPage } from '@/features/offboarding/ExitDetailPage'
import { AssetsPage } from '@/features/employees/AssetsPage'
import { OrganisationPage } from '@/features/organisation/OrganisationPage'
import { PlanPage } from '@/features/organisation/PlanPage'
import { SetupPage } from '@/features/organisation/SetupPage'
import { SuspendedPage } from '@/features/organisation/SuspendedPage'
import { PayrollPage } from '@/features/payroll/PayrollPage'
import { PayrollRunDetailPage } from '@/features/payroll/PayrollRunDetailPage'
import { PayrollSettingsPage } from '@/features/payroll/PayrollSettingsPage'
import { PackagesPage } from '@/features/payroll/PackagesPage'
import { PayslipDetailPage, PayslipsPage } from '@/features/payroll/PayslipsPage'
import { AnalyticsPage } from '@/features/analytics/AnalyticsPage'
import { AuditPage } from '@/features/audit/AuditPage'
import { NotificationSettingsPage } from '@/features/notifications/NotificationSettingsPage'
import { PlatformLoginPage } from '@/features/platform/PlatformLoginPage'
import { NotFoundPage, PlaceholderPage } from '@/features/misc/Placeholder'

/**
 * Which of the two products this browser draws.
 *
 * TWO TREES, CHOSEN BETWEEN — not one tree with platform routes guarded
 * inside it. A SaaS operator gets the console and cannot reach an HR route
 * because no HR route exists in their tree; a customer's user gets the HR
 * application and `/platform/...` is simply not a page there. Neither is the
 * security boundary: the API refuses the platform routes to an organization
 * user on the `platform_only` view flag, and every tenant queryset resolves to
 * nothing for an operator, who holds no grant in any organization.
 *
 * `isPlatformAdmin` is false before the snapshot loads (DENY_ALL) and false
 * for an anonymous visitor, so the sign-in pages — all three entrances — live
 * in the organization tree. The switch flips the moment the snapshot says the
 * session belongs to an operator, which is why the platform sign-in page
 * navigates to `/platform` and the platform tree catches everything else.
 */
export function AppRoutes() {
  const permissions = usePermissions()
  return permissions.isPlatformAdmin ? <PlatformRoutes /> : <OrganizationRoutes />
}

function OrganizationRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RequireAnonymous>
            <LoginPage />
          </RequireAnonymous>
        }
      />
      <Route
        path="/login/admin"
        element={
          <RequireAnonymous>
            <LoginPage entrance="admin" />
          </RequireAnonymous>
        }
      />
      {/*
        The operator's entrance. It lives in this tree because nobody is a
        platform admin until they have signed in — and it is its own page
        rather than a third variant of `LoginPage`, which paints the resolved
        organization's logo and name across half the screen. See the page.
      */}
      <Route
        path="/login/platform"
        element={
          <RequireAnonymous>
            <PlatformLoginPage />
          </RequireAnonymous>
        }
      />

      {/*
        Public. A candidate holding a job's application link — no account, no
        shell. Everything about what they may see and do is decided by the
        server from the token.
      */}
      <Route path="/apply/:token" element={<ApplyPage />} />
      <Route path="/interview-slot/:token" element={<SlotPage />} />

      {/*
        Bare — no AppShell, no navigation. While a temporary password is in
        force this is the only page, and it should look like it.
      */}
      <Route
        path="/change-password"
        element={
          <RequireAuth>
            <ChangePasswordPage />
          </RequireAuth>
        }
      />

      {/*
        Bare too. Straight after the first password reset this is the only
        page until the handbook is acknowledged — a document, not an app.
      */}
      <Route
        path="/handbook"
        element={
          <RequireAuth>
            <HandbookPage />
          </RequireAuth>
        }
      />

      {/*
        Bare, like the two above, and for the sharpest version of the same
        reason: while the organization is stopped the application shell is
        exactly what is NOT available. Rendering navigation around this
        message would offer a menu of pages the API refuses.
      */}
      <Route
        path="/suspended"
        element={
          <RequireAuth>
            <SuspendedPage />
          </RequireAuth>
        }
      />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />

        {/* Recruitment */}
        <Route
          path="recruitment/pipeline"
          element={
            <RequirePermission resource={RESOURCE.APPLICATION}>
              <PipelinePage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/applications/:id"
          element={
            <RequirePermission resource={RESOURCE.APPLICATION}>
              <ApplicationDetailPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/jobs"
          element={
            <RequirePermission resource={RESOURCE.JOB_OPENING}>
              <JobsPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/jobs/:id"
          element={
            <RequirePermission resource={RESOURCE.JOB_OPENING}>
              <JobDetailPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/candidates"
          element={
            <RequirePermission resource={RESOURCE.CANDIDATE}>
              <CandidatesPage />
            </RequirePermission>
          }
        />
        {/*
          Declared BEFORE `candidates/:id`, or "import" is swallowed as a
          candidate id and the wizard becomes a 404-shaped profile page.
        */}
        <Route
          path="recruitment/candidates/import"
          element={
            <RequirePermission resource={RESOURCE.CANDIDATE} action={ACTION.IMPORT}>
              <ImportCandidatesPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/candidates/:id"
          element={
            <RequirePermission resource={RESOURCE.CANDIDATE}>
              <CandidateProfilePage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/interviews"
          element={
            <RequirePermission resource={RESOURCE.INTERVIEW}>
              <InterviewsPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/decisions"
          element={
            <RequirePermission resource={RESOURCE.APPLICATION}>
              <DecisionQueuePage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/offers"
          element={
            <RequirePermission resource={RESOURCE.OFFER}>
              <OffersPage />
            </RequirePermission>
          }
        />
        <Route
          path="recruitment/workflows"
          element={
            <RequirePermission resource={RESOURCE.HIRING_WORKFLOW}>
              <WorkflowsPage />
            </RequirePermission>
          }
        />

        {/* People */}
        <Route
          path="employees"
          element={
            <RequirePermission resource={RESOURCE.EMPLOYEE}>
              <EmployeesPage />
            </RequirePermission>
          }
        />
        <Route
          path="employees/:id"
          element={
            <RequirePermission resource={RESOURCE.EMPLOYEE}>
              <EmployeeDetailPage />
            </RequirePermission>
          }
        />
        <Route path="me" element={<MyProfilePage />} />
        {/*
          Declared BEFORE nothing in particular — `documents` collides with no
          parameterised sibling — but gated on VIEW rather than on a management
          action, because an employee reaching it sees their own documents and
          that is a legitimate use of the page.
        */}
        <Route
          path="documents"
          element={
            <RequirePermission resource={RESOURCE.EMPLOYEE_DOCUMENT}>
              <DocumentsPage />
            </RequirePermission>
          }
        />
        <Route
          path="onboarding"
          element={
            <RequirePermission resource={RESOURCE.ONBOARDING} action={ACTION.EDIT}>
              <OnboardingDashboard />
            </RequirePermission>
          }
        />
        <Route
          path="offboarding"
          element={
            <RequirePermission resource={RESOURCE.OFFBOARDING}>
              <OffboardingPage />
            </RequirePermission>
          }
        />
        <Route
          path="offboarding/:id"
          element={
            <RequirePermission resource={RESOURCE.OFFBOARDING}>
              <ExitDetailPage />
            </RequirePermission>
          }
        />
        <Route
          path="assets"
          element={
            <RequirePermission resource={RESOURCE.ASSET}>
              <AssetsPage />
            </RequirePermission>
          }
        />
        <Route
          path="organisation"
          element={
            <RequirePermission resource={RESOURCE.DEPARTMENT}>
              <OrganisationPage />
            </RequirePermission>
          }
        />
        {/*
          Inside the shell, unlike /suspended: an organization in setup is
          WORKING, and the administrator walking the checklist needs the
          navigation to reach the pages each step links to. ORG_SETTINGS is
          the same resource the endpoint behind it requires.
        */}
        <Route
          path="setup"
          element={
            <RequirePermission resource={RESOURCE.ORG_SETTINGS}>
              <SetupPage />
            </RequirePermission>
          }
        />
        {/*
          VIEW, not EDIT, and there is no write route beside it: the plan is
          read-only to the customer by design. Changing it is a commercial
          conversation, and the endpoint offers nothing to change it with.
        */}
        <Route
          path="plan"
          element={
            <RequirePermission resource={RESOURCE.ORG_SETTINGS}>
              <PlanPage />
            </RequirePermission>
          }
        />

        {/*
         * Modules whose APIs are later phases. These routes exist so the
         * navigation is honest about what is coming rather than 404-ing on a
         * link the sidebar itself rendered.
         */}
        {/* Literal before the sibling route, as everywhere else. */}
        <Route
          path="attendance/essl"
          element={
            <RequirePermission resource={RESOURCE.ATTENDANCE_DEVICE}>
              <EsslIntegrationPage />
            </RequirePermission>
          }
        />
        <Route
          path="attendance/employee/:id"
          element={
            <RequirePermission resource={RESOURCE.ATTENDANCE}>
              <EmployeeMonthPage />
            </RequirePermission>
          }
        />
        <Route
          path="attendance"
          element={
            <RequirePermission resource={RESOURCE.ATTENDANCE}>
              <AttendancePage />
            </RequirePermission>
          }
        />
        <Route
          path="leave"
          element={
            <RequirePermission resource={RESOURCE.LEAVE_REQUEST}>
              <LeavePage />
            </RequirePermission>
          }
        />
        <Route
          path="payroll"
          element={
            <RequirePermission resource={RESOURCE.PAYROLL_RUN}>
              <PayrollPage />
            </RequirePermission>
          }
        />
        <Route
          path="payroll/settings"
          element={
            <RequirePermission resource={RESOURCE.STATUTORY_CONFIG}>
              <PayrollSettingsPage />
            </RequirePermission>
          }
        />
        <Route
          path="payroll/packages"
          element={
            <RequirePermission resource={RESOURCE.PACKAGE}>
              <PackagesPage />
            </RequirePermission>
          }
        />
        <Route
          path="payroll/:id"
          element={
            <RequirePermission resource={RESOURCE.PAYROLL_RUN}>
              <PayrollRunDetailPage />
            </RequirePermission>
          }
        />
        <Route
          path="payslips"
          element={
            <RequirePermission resource={RESOURCE.PAYSLIP}>
              <PayslipsPage />
            </RequirePermission>
          }
        />
        <Route
          path="payslips/:id"
          element={
            <RequirePermission resource={RESOURCE.PAYSLIP}>
              <PayslipDetailPage />
            </RequirePermission>
          }
        />
        <Route
          path="reports"
          element={
            <RequirePermission resource={RESOURCE.REPORT}>
              <AnalyticsPage />
            </RequirePermission>
          }
        />
        <Route
          path="settings/notifications"
          element={
            <RequirePermission resource={RESOURCE.NOTIFICATION}>
              <NotificationSettingsPage />
            </RequirePermission>
          }
        />
        <Route
          path="audit"
          element={
            <RequirePermission resource={RESOURCE.AUDIT_LOG}>
              <AuditPage />
            </RequirePermission>
          }
        />
        <Route
          path="settings"
          element={
            <RequirePermission resource={RESOURCE.ORG_SETTINGS} action={ACTION.EDIT}>
              <PlaceholderPage title="Settings" phase="Organisation settings" />
            </RequirePermission>
          }
        />

        <Route path="*" element={<NotFoundPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
