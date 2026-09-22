/**
 * The platform console, and the line between it and the HR application.
 *
 * THE FIRST TWO TESTS ARE THE POINT. The console is not the HR app with some
 * routes guarded: it is a second route tree the router chooses INSTEAD OF the
 * first one. So what is asserted is mutual absence — an operator's browser is
 * never handed an HR route, and a customer's user is never handed a console
 * route — because that is the property a later change could quietly break. A
 * guard on a route can be forgotten on the next route added; a tree that does
 * not contain the pages cannot be.
 *
 * NONE OF THIS IS THE SECURITY BOUNDARY, and the tests do not pretend
 * otherwise. `platform_only` on the view and `RBACPermission` refuse an
 * organization user at the API, every tenant queryset resolves to nothing for
 * an operator who holds no grant anywhere, and both hold whatever this SPA
 * renders. These tests cover which of two products a browser draws.
 *
 * The third asserts the three sign-in doors actually differ, since a single
 * mistyped path would send a SaaS operator's credentials through the
 * customer entrance and quietly work.
 */

import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '@/components/ui/Toast'
import { ApiError } from '@/lib/api'

const session = vi.hoisted(() => ({ isPlatformAdmin: true }))
const api = vi.hoisted(() => ({
  gets: [] as string[],
  posts: [] as Array<{ url: string; body: unknown }>,
  responses: {} as Record<string, unknown>,
  postResult: null as unknown,
  postError: null as unknown,
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      api.gets.push(url)
      const key = Object.keys(api.responses).find((candidate) => url.startsWith(candidate))
      return key ? api.responses[key] : { data: [], meta: { next: null, previous: null, page_size: 40 } }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      api.posts.push({ url, body })
      if (api.postError) throw api.postError
      // A sign-in answers with a token; everything else with whatever the
      // test staged.
      if (url.startsWith('/auth/login')) {
        return { access: 'test-access-token', must_change_password: false }
      }
      return api.postResult
    }),
  }
})

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { makeSnapshot, snapshotForRole } = await import('./helpers')
  const permissions = () =>
    new Permissions(
      session.isPlatformAdmin
        ? // An operator as the server actually describes one: the flag set,
          // and NOT ONE GRANT anywhere. If the console needed a grant to
          // render, it would be reading a customer's authority to decide what
          // an operator may see.
          makeSnapshot({ is_platform_admin: true, grants: {}, roles: [], features: [] })
        : snapshotForRole('admin'),
    )
  return {
    useAuth: () => ({
      status: 'authenticated',
      mustChangePassword: false,
      user: {
        email: 'ops@platform.test',
        full_name: 'Ops',
        onboarding_pending: false,
        handbook_acknowledgement_pending: false,
      },
      logout: vi.fn(async () => undefined),
      login: vi.fn(async () => undefined),
      permissions: permissions(),
    }),
    usePermissions: permissions,
  }
})

import { AppRoutes } from '@/app/router'
import { PlatformOrganizationsPage } from '@/features/platform/OrganizationsPage'
import { PlatformOrganizationDetailPage } from '@/features/platform/OrganizationDetailPage'

const ORGANIZATIONS = {
  data: [
    {
      id: 'org-1',
      name: 'Northwind Care',
      legal_name: 'Northwind Care Pvt Ltd',
      slug: 'northwind-care',
      status: 'active',
      is_operational: true,
      primary_email: 'admin@northwind.test',
      phone: '',
      city: 'Pune',
      state: 'MH',
      country: 'IN',
      timezone: 'Asia/Kolkata',
      currency: 'INR',
      employee_count: 118,
      member_count: 120,
      subscription: {
        id: 'sub-1',
        plan_code: 'growth',
        plan_name: 'Growth',
        status: 'active',
        employee_limit: 150,
        employee_limit_override: null,
        override_reason: '',
        enabled_features: ['core', 'payroll'],
        started_at: '2026-01-04T00:00:00Z',
        ends_at: null,
        cancelled_at: null,
        read_only_grace_days: 30,
        features_narrowed_at: null,
      },
      created_at: '2026-01-04T00:00:00Z',
    },
  ],
  meta: { next: null, previous: null, page_size: 40 },
}

const PLANS = {
  data: [
    {
      id: 'plan-1',
      code: 'starter',
      name: 'Starter',
      description: 'Core HR.',
      employee_limit: 25,
      disabled_features: ['payroll'],
      enabled_features: ['core', 'leave'],
      storage_limit_mb: 2048,
      support_level: 'standard',
      is_public: true,
      display_order: 1,
      subscriber_count: 4,
    },
  ],
  meta: { next: null, previous: null, page_size: 40 },
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <AppRoutes />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * One page under a route pattern, so `useParams` resolves — a detail page
 * rendered bare reads no id, disables its query, and passes every assertion
 * about an absence for the wrong reason.
 */
function renderPage(
  ui: ReactElement,
  path = '/platform/organizations',
  pattern = '/platform/organizations',
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <Routes>
            <Route path={pattern} element={ui} />
          </Routes>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  session.isPlatformAdmin = true
  api.gets = []
  api.posts = []
  api.postError = null
  api.postResult = null
  api.responses = {
    '/platform/organizations/org-1': ORGANIZATIONS.data[0],
    '/platform/organizations': ORGANIZATIONS,
    '/platform/plans': PLANS,
    '/platform/summary': {
      organizations_total: 3,
      organizations_operational: 2,
      by_status: { active: 2, suspended: 1 },
    },
  }
})

describe('the two route trees are disjoint', () => {
  it('gives an operator the console even at an HR address, and no HR navigation', async () => {
    renderAt('/employees')

    // The console, reached from a URL that is an HR page in the other tree.
    expect(await screen.findByText(/Platform console/i)).toBeInTheDocument()
    // And none of the HR shell: no sidebar, so no link into a customer's
    // people, payroll or candidates to click by accident.
    expect(screen.queryByLabelText('Main navigation')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Employees' })).not.toBeInTheDocument()
  })

  it('does not give an organization user the console', async () => {
    session.isPlatformAdmin = false
    renderAt('/platform/organizations')

    // The HR tree has no such route, so its catch-all answers. What matters
    // is the absence: no console chrome, no organization list, nothing that
    // reads another customer's name.
    await waitFor(() =>
      expect(screen.queryByText(/Platform console/i)).not.toBeInTheDocument(),
    )
    expect(screen.queryByText('Northwind Care')).not.toBeInTheDocument()
    // And nothing was fetched from the platform API on their behalf.
    expect(api.gets.filter((url) => url.startsWith('/platform/'))).toHaveLength(0)
    // The positive control, without which this test would pass just as well
    // if the app had rendered nothing at all: they got the HR application.
    expect(await screen.findByLabelText('Main navigation')).toBeInTheDocument()
  })

  it('sends an operator to the console rather than the HR dashboard', async () => {
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.getByText('Organizations')).toBeInTheDocument()
  })
})

describe('the sign-in entrances', () => {
  it('posts each entrance to its own door', async () => {
    // The real AuthProvider, not the mocked one: the mapping from entrance to
    // URL is the thing under test, and a single mistyped path would send an
    // operator's credentials through the customer door and work.
    const { AuthProvider, useAuth } = await vi.importActual<
      typeof import('@/app/AuthProvider')
    >('@/app/AuthProvider')

    function Doors() {
      const { login } = useAuth()
      return (
        <>
          <button onClick={() => void login('a@b.test', 'pw')}>customer</button>
          <button onClick={() => void login('a@b.test', 'pw', 'admin')}>admin</button>
          <button onClick={() => void login('a@b.test', 'pw', 'platform')}>platform</button>
        </>
      )
    }

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <Doors />
        </AuthProvider>
      </QueryClientProvider>,
    )

    await userEvent.click(screen.getByText('customer'))
    await userEvent.click(screen.getByText('admin'))
    await userEvent.click(screen.getByText('platform'))

    const logins = api.posts.map((post) => post.url).filter((url) => url.startsWith('/auth/login'))
    expect(logins).toEqual([
      '/auth/login/',
      '/auth/login/admin/',
      '/auth/login/platform/',
    ])
  })
})

describe('the organization list', () => {
  it('shows commercial metadata and no person', async () => {
    renderPage(<PlatformOrganizationsPage />)

    expect(await screen.findByText('Northwind Care')).toBeInTheDocument()
    // A headcount against the limit it is measured against — the number that
    // answers "are they about to be refused their next hire".
    expect(screen.getByText('118 / 150')).toBeInTheDocument()
    expect(screen.getByText('Growth')).toBeInTheDocument()
  })
})

describe('provisioning a customer', () => {
  it('sends one request and never displays a password', async () => {
    api.postResult = {
      ...ORGANIZATIONS.data[0],
      id: 'org-2',
      name: 'Bluebird Retail',
      admin_email: 'admin@bluebird.test',
      invitation_sent: true,
    }
    renderPage(<PlatformOrganizationsPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'New organization' }))
    await userEvent.type(screen.getByLabelText(/Organization name/), 'Bluebird Retail')
    await userEvent.type(screen.getByLabelText(/Administrator email/), 'admin@bluebird.test')
    await userEvent.click(screen.getByRole('button', { name: 'Create organization' }))

    await waitFor(() => expect(api.posts).toHaveLength(1))
    expect(api.posts[0].url).toBe('/platform/organizations/')
    expect(api.posts[0].body).toMatchObject({
      name: 'Bluebird Retail',
      admin_email: 'admin@bluebird.test',
    })
    // The response carries no password, so neither can the screen. What the
    // operator is told is whether the invitation actually went.
    expect(await screen.findByText(/invitation was sent to admin@bluebird.test/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/password:/i)
  })

  it('says so plainly when the invitation could not be sent', async () => {
    api.postResult = {
      ...ORGANIZATIONS.data[0],
      id: 'org-3',
      name: 'Cedar Labs',
      admin_email: 'admin@cedar.test',
      invitation_sent: false,
    }
    renderPage(<PlatformOrganizationsPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'New organization' }))
    await userEvent.type(screen.getByLabelText(/Organization name/), 'Cedar Labs')
    await userEvent.type(screen.getByLabelText(/Administrator email/), 'admin@cedar.test')
    await userEvent.click(screen.getByRole('button', { name: 'Create organization' }))

    // Not "created successfully" — the credential is stored nowhere, so an
    // operator who is not told this has a customer who cannot sign in.
    expect(await screen.findByText(/could not be sent/i)).toBeInTheDocument()
  })

  it("shows the service's refusal instead of a message of its own", async () => {
    api.postError = new ApiError(422, {
      error: { code: 'business_rule', message: 'That slug is already taken.' },
    })
    renderPage(<PlatformOrganizationsPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'New organization' }))
    await userEvent.type(screen.getByLabelText(/Organization name/), 'Northwind Care')
    await userEvent.type(screen.getByLabelText(/Administrator email/), 'a@b.test')
    await userEvent.click(screen.getByRole('button', { name: 'Create organization' }))

    expect(await screen.findByText('That slug is already taken.')).toBeInTheDocument()
  })
})

describe('changing what a customer is on', () => {
  it("prints the service's sentence when a downgrade is refused", async () => {
    api.postError = new ApiError(422, {
      error: {
        code: 'business_rule',
        message: 'This plan allows 25 active employees; this organization has 118.',
      },
    })
    renderPage(
      <PlatformOrganizationDetailPage />,
      '/platform/organizations/org-1',
      '/platform/organizations/:id',
    )

    expect(await screen.findByText('Northwind Care')).toBeInTheDocument()
    const planCard = within(
      (await screen.findByRole('button', { name: 'Change plan' })).closest(
        'section',
      ) as HTMLElement,
    )
    await userEvent.selectOptions(planCard.getByLabelText('Plan'), 'starter')
    await userEvent.click(planCard.getByRole('button', { name: 'Change plan' }))

    // The rule lives in the service, so the console repeats it rather than
    // re-deriving a second opinion that can drift from the first.
    expect(
      await screen.findByText(
        'This plan allows 25 active employees; this organization has 118.',
      ),
    ).toBeInTheDocument()
    expect(api.posts[0].url).toBe('/platform/organizations/org-1/change-plan/')
  })

  it('requires a reason before a seat override can be saved', async () => {
    renderPage(
      <PlatformOrganizationDetailPage />,
      '/platform/organizations/org-1',
      '/platform/organizations/:id',
    )

    const save = await screen.findByRole('button', { name: 'Save override' })
    // The API and a database constraint both demand it; the form agrees so the
    // operator finds out before the round trip, not after.
    expect(save).toBeDisabled()

    // Scoped to this card: all three forms carry a Reason, because all three
    // changes have to be answerable a year later.
    const card = within(save.closest('section') as HTMLElement)
    await userEvent.type(card.getByLabelText(/Employee limit/), '400')
    await userEvent.type(card.getByLabelText(/Reason/), 'Agreed overage, contract 2026-11')
    expect(card.getByRole('button', { name: 'Save override' })).toBeEnabled()
  })
})

describe('resending an administrator invitation', () => {
  function detailWith(pending: boolean) {
    api.responses['/platform/organizations/org-1'] = {
      ...ORGANIZATIONS.data[0],
      admin_invitation_pending: pending,
    }
    renderPage(
      <PlatformOrganizationDetailPage />,
      '/platform/organizations/org-1',
      '/platform/organizations/:id',
    )
  }

  it('offers the resend only while an administrator has never signed in', async () => {
    detailWith(false)

    // A working customer administrator is the customer's to recover. The
    // console does not offer the lever, and the service would refuse it.
    expect(
      await screen.findByText(/Every administrator has signed in/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resend invitation' })).not.toBeInTheDocument()
  })

  it('confirms first, then reports who was mailed', async () => {
    api.postResult = { invitations: [{ email: 'admin@northwind.test', sent: true }] }
    detailWith(true)

    await userEvent.click(await screen.findByRole('button', { name: 'Resend invitation' }))
    // Nothing is sent until the operator confirms: a resend invalidates the
    // previous temporary password.
    expect(api.posts).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: 'Resend' }))

    await waitFor(() => expect(api.posts).toHaveLength(1))
    expect(api.posts[0].url).toBe('/platform/organizations/org-1/resend-invitation/')
    expect(await screen.findByText('Sent to admin@northwind.test.')).toBeInTheDocument()
  })

  it('never claims an invitation went when it did not', async () => {
    api.postResult = { invitations: [{ email: 'admin@northwind.test', sent: false }] }
    detailWith(true)

    await userEvent.click(await screen.findByRole('button', { name: 'Resend invitation' }))
    await userEvent.click(screen.getByRole('button', { name: 'Resend' }))

    expect(await screen.findByText(/Could not be sent to admin@northwind.test/)).toBeInTheDocument()
    expect(screen.queryByText('Invitation resent.')).not.toBeInTheDocument()
    expect(screen.queryByText('Sent to admin@northwind.test.')).not.toBeInTheDocument()
  })
})

describe('the end of a customer', () => {
  function detailAs(overrides: Record<string, unknown>) {
    api.responses['/platform/organizations/org-1'] = {
      ...ORGANIZATIONS.data[0],
      admin_invitation_pending: false,
      archived_at: null,
      purged_at: null,
      ...overrides,
    }
    renderPage(
      <PlatformOrganizationDetailPage />,
      '/platform/organizations/org-1',
      '/platform/organizations/:id',
    )
  }

  it('offers archiving only for a cancelled organization, and needs a reason', async () => {
    api.postResult = { ...ORGANIZATIONS.data[0], status: 'archived' }
    detailAs({ status: 'cancelled' })

    const button = await screen.findByRole('button', { name: 'Archive organization' })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Reason for archiving/), 'Contract ended')
    await userEvent.click(screen.getByRole('button', { name: 'Archive organization' }))

    await waitFor(() => expect(api.posts).toHaveLength(1))
    expect(api.posts[0]).toEqual({
      url: '/platform/organizations/org-1/archive/',
      body: { reason: 'Contract ended' },
    })
  })

  it('never offers archiving to a live customer', async () => {
    detailAs({ status: 'active' })

    expect(await screen.findByText('Lifecycle')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archive organization' })).not.toBeInTheDocument()
  })

  it('has no purge button, and says where purging is done', async () => {
    detailAs({ status: 'archived', archived_at: '2025-01-10T00:00:00Z' })

    expect(await screen.findByText(/Purging is not available here/)).toBeInTheDocument()
    expect(screen.getByText(/manage.py purge_organization northwind-care/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /purge/i })).not.toBeInTheDocument()
  })
})

describe('support access, from the console', () => {
  function withGrants(grants: unknown[], configuration: unknown = null) {
    // Specific keys first: the mock answers the first prefix that matches.
    api.responses = {
      ...(configuration
        ? { '/platform/support-grants/g-1/configuration': configuration }
        : {}),
      '/platform/support-grants': { data: grants, meta: { next: null, previous: null, page_size: 40 } },
      ...api.responses,
      '/platform/organizations/org-1': {
        ...ORGANIZATIONS.data[0],
        admin_invitation_pending: false,
        archived_at: null,
        purged_at: null,
      },
    }
    renderPage(
      <PlatformOrganizationDetailPage />,
      '/platform/organizations/org-1',
      '/platform/organizations/:id',
    )
  }

  const GRANT = {
    id: 'g-1',
    organization_slug: 'northwind-care',
    organization_name: 'Northwind Care',
    requested_by_email: 'ops@platform.test',
    reason: 'Leave balances look wrong since the policy change.',
    decided_at: null,
    revoked_at: null,
    created_at: '2026-09-22T08:00:00Z',
  }

  it('asks with a reason, and asks only', async () => {
    api.postResult = { ...GRANT, status: 'requested', expires_at: null, usable: false }
    withGrants([])

    const button = await screen.findByRole('button', { name: 'Request access' })
    expect(button).toBeDisabled()
    await userEvent.type(
      screen.getByLabelText(/Why you need to see their configuration/),
      'Leave balances look wrong since the policy change.',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Request access' }))

    await waitFor(() => expect(api.posts).toHaveLength(1))
    expect(api.posts[0].url).toBe('/platform/organizations/org-1/support-grants/')
    // Nothing to view: the customer has not decided.
    expect(screen.queryByRole('button', { name: 'View configuration' })).not.toBeInTheDocument()
  })

  it('waits while the customer decides', async () => {
    withGrants([{ ...GRANT, status: 'requested', expires_at: null, usable: false }])

    expect(await screen.findByText(/Waiting for the customer's administrator/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View configuration' })).not.toBeInTheDocument()
  })

  it('shows the configuration once approved', async () => {
    withGrants(
      [{ ...GRANT, status: 'approved', expires_at: '2026-09-23T08:00:00Z', usable: true }],
      {
        organization: 'northwind-care',
        grant: 'g-1',
        expires_at: '2026-09-23T08:00:00Z',
        tables: { 'accounts.Role': [{ code: 'hr_head' }], 'leave.LeaveType': [] },
      },
    )

    await userEvent.click(await screen.findByRole('button', { name: 'View configuration' }))

    expect(await screen.findByText('accounts.Role')).toBeInTheDocument()
    expect(screen.getByText('leave.LeaveType')).toBeInTheDocument()
  })
})
