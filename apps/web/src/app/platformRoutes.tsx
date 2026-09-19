/**
 * The console's route tree.
 *
 * DISJOINT FROM THE HR APPLICATION'S, and that is the whole design of this
 * file. It is not the HR tree with platform routes bolted on and a guard in
 * front of them: it is a separate `<Routes>` that the router chooses INSTEAD
 * OF the other one. An operator's browser is never handed a component that
 * fetches an employee, a payslip or a candidate, because no such route exists
 * in this tree to reach.
 *
 * Why disjointness rather than guards. A guard is a condition, and a condition
 * is something a later change can get wrong: one route added without its
 * wrapper, one `hasFeature` clause inverted, and an HR page renders for the
 * wrong principal. Two trees have no condition to get wrong — the HR pages are
 * not reachable from here by any URL, correct guard or not.
 *
 * NONE OF WHICH IS THE SECURITY BOUNDARY, as everywhere else in this app. The
 * platform routes are refused to an organization user by `RBACPermission` and
 * the `platform_only` view flag; every tenant queryset resolves to nothing for
 * an operator, who holds no grant in any organization. What this file decides
 * is which of two products a browser draws.
 *
 * `/change-password` is the one route both trees carry, because a freshly
 * bootstrapped operator signs in with a temporary password and the gate in
 * `RequireAuth` sends them there. Without it they would be redirected to a
 * route that does not exist in their tree and bounced straight back.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from './guards'
import { PlatformShell } from '@/features/platform/PlatformShell'
import { PlatformSummaryPage } from '@/features/platform/SummaryPage'
import { PlatformOrganizationsPage } from '@/features/platform/OrganizationsPage'
import { PlatformOrganizationDetailPage } from '@/features/platform/OrganizationDetailPage'
import { PlatformPlansPage } from '@/features/platform/PlansPage'
import { ChangePasswordPage } from '@/features/auth/ChangePasswordPage'

export function PlatformRoutes() {
  return (
    <Routes>
      {/* Bare, exactly as in the other tree: while a temporary password is in
          force this is the only page, and it should look like it. */}
      <Route
        path="/change-password"
        element={
          <RequireAuth>
            <ChangePasswordPage />
          </RequireAuth>
        }
      />

      <Route
        path="/platform"
        element={
          <RequireAuth>
            <PlatformShell />
          </RequireAuth>
        }
      >
        <Route index element={<PlatformSummaryPage />} />
        <Route path="organizations" element={<PlatformOrganizationsPage />} />
        <Route path="organizations/:id" element={<PlatformOrganizationDetailPage />} />
        <Route path="plans" element={<PlatformPlansPage />} />
      </Route>

      {/*
        Everything else lands on the console, including `/` — an operator who
        followed a customer's link, or who was left on `/login/platform` by the
        sign-in redirect, belongs here rather than on a 404 for a page that is
        not part of their product.
      */}
      <Route path="*" element={<Navigate to="/platform" replace />} />
    </Routes>
  )
}
