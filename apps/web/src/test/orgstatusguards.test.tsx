/**
 * Where the organization's own state sends a signed-in user.
 *
 * Two gates, and they are not about permissions. A suspended customer's users
 * keep every grant their roles carry; a new customer's administrator holds
 * everything and has nothing configured to use it on. So neither is expressible
 * as `can(...)`, and both live in the RequireAuth chain beside the password and
 * onboarding redirects.
 *
 * NEITHER IS A SECURITY BOUNDARY. The API refuses a stopped organization on
 * every route but three, with its own code, whatever this file does. What the
 * guards buy is a person seeing the reason rather than eleven failing pages.
 *
 * PRECEDENCE IS THE INTERESTING PART, so it is asserted directly: suspension
 * outranks the password gate, because `/change-password` is one of the three
 * routes a suspended account may still reach and changing a password to enter
 * an application you cannot use is not a better first screen than being told
 * why.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const auth = vi.hoisted(() => ({
  status: 'authenticated' as 'authenticated' | 'anonymous' | 'booting',
  mustChangePassword: false,
  onboardingPending: false,
  organizationStatus: 'active' as string,
  role: 'admin' as 'admin' | 'employee',
}))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  const permissions = () =>
    new Permissions({
      ...snapshotForRole(auth.role),
      organization_status: auth.organizationStatus as never,
    })
  return {
    useAuth: () => ({
      status: auth.status,
      mustChangePassword: auth.mustChangePassword,
      user: {
        email: 'asha@example.test',
        onboarding_pending: auth.onboardingPending,
        handbook_acknowledgement_pending: false,
      },
      logout: vi.fn(async () => undefined),
      permissions: permissions(),
    }),
    usePermissions: permissions,
  }
})

import { RequireAuth } from '@/app/guards'

/**
 * Every destination the chain can choose, each saying which one it is.
 *
 * A single catch-all route would render the same text wherever the guard sent
 * the user, so every assertion would pass -- which is exactly what the first
 * version of this file did.
 */
const DESTINATIONS = [
  ['/suspended', 'the suspension screen'],
  ['/change-password', 'the password screen'],
  ['/handbook', 'the handbook'],
  ['/me', 'my profile'],
  ['/setup', 'the setup checklist'],
] as const

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          {DESTINATIONS.map(([route, label]) => (
            <Route
              key={route}
              path={route}
              element={
                <RequireAuth>
                  <p>{label}</p>
                </RequireAuth>
              }
            />
          ))}
          <Route
            path="*"
            element={
              <RequireAuth>
                <p>the application</p>
              </RequireAuth>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Which screen the guard chain actually settled on. */
function landedOn(path: string): string {
  const { container } = renderAt(path)
  const text = container.textContent ?? ''
  const match = DESTINATIONS.find(([, label]) => text.includes(label))
  return match ? match[0] : 'the application'
}

function setup(overrides: Partial<typeof auth> = {}) {
  Object.assign(auth, {
    status: 'authenticated',
    mustChangePassword: false,
    onboardingPending: false,
    organizationStatus: 'active',
    role: 'admin',
    ...overrides,
  })
}

describe('an active organization', () => {
  it('is not redirected anywhere', () => {
    setup()

    expect(landedOn('/employees')).toBe('the application')
  })

  it('lets an ordinary employee through as well', () => {
    setup({ role: 'employee' })

    expect(landedOn('/me')).toBe('/me')
  })
})

describe('a stopped organization', () => {
  it.each(['suspended', 'cancelled', 'archived'])(
    'sends %s to the explanation screen',
    (status) => {
      setup({ organizationStatus: status })

      expect(landedOn('/employees')).toBe('/suspended')
    },
  )

  it('renders the screen itself rather than redirecting forever', () => {
    setup({ organizationStatus: 'suspended' })
    renderAt('/suspended')

    // The guard must let /suspended through, or the redirect loops.
    expect(screen.getByText('the suspension screen')).toBeInTheDocument()
  })

  it('outranks the password gate', () => {
    // Both conditions at once, and a genuine choice rather than a technicality:
    // `/change-password` is one of the three routes a suspended account may
    // still reach. It also has to SHORT-CIRCUIT -- when this test was first
    // written the chain fell through from /suspended to the password gate,
    // which redirected back, and the two guards looped until the heap ran out.
    setup({ organizationStatus: 'suspended', mustChangePassword: true })

    expect(landedOn('/change-password')).toBe('/suspended')
    expect(landedOn('/employees')).toBe('/suspended')
  })
})

describe('an organization still in setup', () => {
  it('sends the administrator to the checklist', () => {
    setup({ organizationStatus: 'pending_setup' })

    expect(landedOn('/reports')).toBe('/setup')
  })

  it('leaves an ordinary employee alone', () => {
    // PENDING_SETUP is a working state, not a locked one. An employee cannot
    // finish setup, so sending them to a wizard would replace a usable app
    // with a dead end.
    setup({ organizationStatus: 'pending_setup', role: 'employee' })

    expect(landedOn('/me')).toBe('/me')
    expect(landedOn('/leave')).toBe('the application')
  })

  it('leaves the pages the checklist links to reachable', () => {
    // Each step is done on an existing screen. Bouncing those back to the
    // checklist would trap the administrator in a loop between the list and
    // the pages it points at.
    setup({ organizationStatus: 'pending_setup' })

    expect(landedOn('/setup')).toBe('/setup')
    for (const path of ['/organisation', '/settings', '/employees/new']) {
      expect(landedOn(path)).toBe('the application')
    }
  })

  it('yields to the password gate, which comes first', () => {
    // A temporary password is still a temporary password: the administrator
    // changes it before configuring anything.
    setup({ organizationStatus: 'pending_setup', mustChangePassword: true })

    expect(landedOn('/setup')).toBe('/change-password')
  })

  it('yields to onboarding, which also comes first', () => {
    setup({ organizationStatus: 'pending_setup', onboardingPending: true })

    expect(landedOn('/reports')).toBe('/me')
  })
})
