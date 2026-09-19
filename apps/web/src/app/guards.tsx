/**
 * Route and element guards.
 *
 * Every one of these is a COURTESY. The API re-checks the same permission on
 * the request that the page will make, and returns 403 or 404 to anyone who
 * gets past these by editing the URL, disabling JavaScript, or calling the
 * endpoint directly. What guards buy is a user who is never shown a door that
 * will not open.
 */

import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth, usePermissions } from './AuthProvider'
import { ACTION, RESOURCE, type Action, type Resource } from '@/lib/permissions'
import { Spinner } from '@/components/ui/Spinner'
import { EmptyState } from '@/components/ui/States'

export function BootSplash() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas">
      <div className="flex flex-col items-center gap-3 text-ink-muted">
        <Spinner className="h-6 w-6" label="Restoring your session" />
        <p className="text-sm">Restoring your session…</p>
      </div>
    </div>
  )
}

/**
 * Statuses in which the product is not usable at all.
 *
 * Mirrors the server's `OPERATIONAL_STATUSES` by complement: a suspended,
 * cancelled or archived organization is refused on every route except
 * `/auth/`, `/me/` and `/org/branding/`. Nothing here decides that -- the API
 * answers `organization_suspended` regardless -- but it decides what the user
 * is shown instead of eleven failing pages.
 */
const NOT_OPERATIONAL = ['suspended', 'cancelled', 'archived']

/** Requires a session. Remembers where the user was headed. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, mustChangePassword, user } = useAuth()
  const permissions = usePermissions()
  const location = useLocation()

  if (status === 'booting') return <BootSplash />
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  // FIRST, ahead of even the password gate, because it outranks everything a
  // user could otherwise do here. The API allows a suspended organization
  // exactly three prefixes -- auth, /me/ and branding -- so every other
  // destination, /change-password included, leads somewhere that either fails
  // or accomplishes nothing while the account is stopped. Changing a password
  // to enter an application you cannot use is not a better first screen than
  // being told why.
  // SHORT-CIRCUITS, rather than merely coming first, and the difference is not
  // stylistic: falling through would reach the password gate, which redirects
  // to /change-password, which this check then bounces back to /suspended --
  // a loop between two guards that each think they are right. While the
  // organization is stopped this screen is the only destination, so the chain
  // ends here.
  if (NOT_OPERATIONAL.includes(permissions.organizationStatus)) {
    if (location.pathname === '/suspended') return <>{children}</>
    return <Navigate to="/suspended" replace />
  }
  // A temporary password unlocks exactly one screen. The API refuses every
  // other request anyway (403 password_change_required); this keeps the user
  // from seeing an app full of doors that will not open.
  if (mustChangePassword && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }
  // Straight after the first password reset, the handbook comes first: the
  // employee reads it and acknowledges before anything else, their document
  // uploads included. The acknowledgement itself clears the flag server-side.
  if (
    user?.handbook_acknowledgement_pending &&
    !['/handbook', '/change-password'].includes(location.pathname)
  ) {
    return <Navigate to="/handbook" replace />
  }
  // A new joiner whose mandatory onboarding is outstanding gets exactly one
  // destination: their own profile, where the checklist and document uploads
  // live. The API enforces the same gate (403 onboarding_pending) on every
  // other route, so this is the courtesy, not the lock.
  if (
    user?.onboarding_pending &&
    !['/me', '/change-password', '/handbook'].includes(location.pathname)
  ) {
    return <Navigate to="/me" replace />
  }
  // LAST, and only for the person who can actually finish it. A new customer
  // sits in `pending_setup` until its administrator completes the wizard, and
  // that is a WORKING state rather than a locked one -- the API lets them read
  // and write, because writing is precisely what setup is. So this redirects
  // whoever can configure the organization and leaves everyone else alone:
  // sending an employee to a wizard they cannot use would replace a usable app
  // with a dead end.
  //
  // `/organisation` and `/settings` stay reachable because the wizard's own
  // steps link into them; bouncing back here would trap the administrator in
  // a loop between the checklist and the pages it points at.
  if (
    permissions.organizationStatus === 'pending_setup' &&
    permissions.can(RESOURCE.ORG_SETTINGS, ACTION.EDIT) &&
    !SETUP_ALLOWED.some((prefix) => location.pathname.startsWith(prefix))
  ) {
    return <Navigate to="/setup" replace />
  }
  return <>{children}</>
}

/**
 * Where an administrator mid-setup may still go.
 *
 * The wizard is a progress marker, not a staging area: every step writes to
 * the real tables through the pages that already exist, so those pages have to
 * be reachable while the checklist is outstanding.
 */
const SETUP_ALLOWED = [
  '/setup',
  '/organisation',
  '/settings',
  '/employees',
  '/me',
  '/change-password',
  '/handbook',
]

/** Only reachable when signed out — keeps a signed-in user off the login page. */
export function RequireAnonymous({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  if (status === 'booting') return <BootSplash />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <>{children}</>
}

/**
 * Requires a permission to render a route.
 *
 * Renders an explanation rather than redirecting: bouncing someone to the
 * dashboard for following a colleague's link leaves them with no idea what
 * happened, and they will try again.
 */
export function RequirePermission({
  resource,
  action = ACTION.VIEW,
  children,
}: {
  resource: Resource
  action?: Action
  children: ReactNode
}) {
  const permissions = usePermissions()

  if (!permissions.can(resource, action)) {
    return (
      <EmptyState
        title="You do not have access to this page"
        description={
          'Your role does not include this permission. If you need it, ask your ' +
          'administrator — the request is enforced by the server, not by this screen.'
        }
      />
    )
  }
  return <>{children}</>
}

/**
 * Conditional rendering for a single control.
 *
 *   <Can resource={RESOURCE.APPLICATION} action={ACTION.REJECT}>
 *     <Button variant="danger">Reject candidate</Button>
 *   </Can>
 */
export function Can({
  resource,
  action = ACTION.VIEW,
  fallback = null,
  children,
}: {
  resource: Resource
  action?: Action
  fallback?: ReactNode
  children: ReactNode
}) {
  const permissions = usePermissions()
  return <>{permissions.can(resource, action) ? children : fallback}</>
}

/** Renders only for a read-only principal (CEO), and only for explanatory copy. */
export function WhenReadOnly({ children }: { children: ReactNode }) {
  return usePermissions().isReadOnly ? <>{children}</> : null
}

/** Renders only for principals who can act. */
export function WhenWritable({ children }: { children: ReactNode }) {
  return usePermissions().isReadOnly ? null : <>{children}</>
}
