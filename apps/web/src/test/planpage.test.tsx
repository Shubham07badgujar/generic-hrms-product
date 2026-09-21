/**
 * The plan page, which a customer reads and cannot change.
 *
 * Read-only is the product decision, not an unfinished screen: changing a plan
 * is a commercial conversation, and an upgrade button here would put a billing
 * decision in the hands of whoever holds the Admin role this month. So the
 * first thing asserted is an absence — no control on this page writes
 * anything — and the rest is about the two numbers a customer actually needs:
 * how many seats are left, and which modules they have.
 *
 * Every number comes from the server. `seats_remaining` in particular is the
 * same function the bulk importer gates a batch on, so what a person reads
 * here and what refuses a 200-row staff list cannot drift apart.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { MyPlan } from '@/lib/types'

const state = vi.hoisted(() => ({
  plan: null as unknown,
  exportAvailable: false,
  status: 'active' as string,
  downloads: [] as string[],
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async () => state.plan),
    downloadFile: vi.fn(async (url: string) => {
      state.downloads.push(url)
    }),
  }
})

// The page now asks `/me/` whether to offer the export, so it needs a session.
// The flag is the whole of what these tests vary: the rule behind it is the
// server's, and is tested there.
vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { makeSnapshot } = await import('./helpers')
  const permissions = () =>
    new Permissions(makeSnapshot({ organization_status: state.status as never }))
  return {
    useAuth: () => ({
      user: { email: 'admin@example.test', organization_export_available: state.exportAvailable },
      logout: vi.fn(async () => undefined),
      permissions: permissions(),
    }),
    usePermissions: permissions,
  }
})

import { PlanPage } from '@/features/organisation/PlanPage'
import { SuspendedPage } from '@/features/organisation/SuspendedPage'
import { ToastProvider } from '@/components/ui/Toast'
import userEvent from '@testing-library/user-event'

const STARTER: MyPlan = {
  plan: {
    code: 'starter',
    name: 'Starter',
    description: 'Core HR for a small team.',
    support_level: 'standard',
  },
  status: 'active',
  features: ['core', 'leave', 'attendance'],
  employees_used: 18,
  employee_limit: 25,
  seats_remaining: 7,
  storage_limit_mb: 2048,
}

function renderPlan(plan: MyPlan) {
  state.plan = plan
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <PlanPage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state.plan = STARTER
  state.exportAvailable = false
  state.status = 'active'
  state.downloads = []
})

describe('what a customer sees', () => {
  it('names the plan and its commercial state', async () => {
    renderPlan(STARTER)

    expect(await screen.findByText('Starter')).toBeInTheDocument()
    expect(screen.getByText('Core HR for a small team.')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('reports seats from the server rather than subtracting here', async () => {
    renderPlan(STARTER)

    expect(await screen.findByText('18')).toBeInTheDocument()
    expect(screen.getByText('25')).toBeInTheDocument()
    expect(
      screen.getByText('7 before the next hire is refused'),
    ).toBeInTheDocument()
  })

  it('shows every module, marking what the plan does not include', async () => {
    renderPlan(STARTER)

    const payroll = (await screen.findByText('Payroll and statutory')).closest('li')!
    expect(within(payroll).getByText('not included')).toBeInTheDocument()

    const leave = screen.getByText('Leave').closest('li')!
    expect(within(leave).getByText('included')).toBeInTheDocument()
  })

  it('offers nothing that changes the plan', async () => {
    renderPlan(STARTER)
    await screen.findByText('Starter')

    // The absence IS the feature. A customer upgrading themselves through the
    // HR product would be a billing decision made by whoever holds the role.
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryByText(/upgrade/i)).not.toBeInTheDocument()
  })
})

describe('states that are not "active"', () => {
  it('says a past-due invoice does not stop anyone working', async () => {
    // Locking an HR department out of payroll on the 30th because an invoice
    // is late punishes the employees rather than the buyer, and the backend
    // deliberately does not do it. The copy has to match.
    renderPlan({ ...STARTER, status: 'past_due' })

    expect(await screen.findByText('Past due')).toBeInTheDocument()
    expect(screen.getByText(/Nothing is switched off/)).toBeInTheDocument()
  })

  it('tells a cancelled customer their records can still be exported', async () => {
    renderPlan({ ...STARTER, status: 'cancelled' })

    expect(await screen.findByText('Cancelled')).toBeInTheDocument()
    expect(screen.getByText(/still export them/)).toBeInTheDocument()
  })
})

describe('a deployment that sells nothing', () => {
  it('reads as unlimited rather than as an error', async () => {
    // A self-hosted single-company install has no plans at all. Reporting that
    // as a missing subscription would make an ordinary state look broken.
    renderPlan({
      plan: null,
      status: null,
      features: ['core', 'leave', 'payroll'],
      employees_used: 40,
      employee_limit: null,
      seats_remaining: null,
      storage_limit_mb: null,
    })

    expect(await screen.findByText('No plan')).toBeInTheDocument()
    expect(screen.getAllByText('Unlimited').length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText('40')).toBeInTheDocument()
  })
})

describe('a feature this build has no name for', () => {
  it('is shown as its code rather than dropped', async () => {
    // A module the customer is paying for must not be invisible because the
    // SPA is one release behind the plan catalogue.
    renderPlan({ ...STARTER, features: [...STARTER.features, 'timesheets' as never] })

    expect(await screen.findByText('timesheets')).toBeInTheDocument()
  })
})

describe("taking the organization's whole record", () => {
  function renderSuspended() {
    return render(
      <MemoryRouter>
        <ToastProvider>
          <SuspendedPage />
        </ToastProvider>
      </MemoryRouter>,
    )
  }

  it('offers the download on the plan page only to whoever the route would serve', async () => {
    state.exportAvailable = true
    renderPlan(STARTER)

    const button = await screen.findByRole('button', { name: 'Download your data' })
    await userEvent.click(button)
    // Through the authenticated client: the token lives in memory, so a plain
    // link would arrive without credentials.
    expect(state.downloads).toEqual(['/org/export/'])
  })

  it('does not offer it when the server says no', async () => {
    renderPlan(STARTER)

    expect(await screen.findByText('Starter')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download your data' })).not.toBeInTheDocument()
  })

  it('offers it on the cancelled screen, beside signing out', () => {
    state.status = 'cancelled'
    state.exportAvailable = true
    renderSuspended()

    expect(screen.getByText('This account has been cancelled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download your data' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('does not offer it to a suspended organization, whose remedy is restoring', () => {
    // The server answers false for suspension; the screen shows what it says.
    state.status = 'suspended'
    state.exportAvailable = false
    renderSuspended()

    expect(screen.getByText('This account is suspended')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download your data' })).not.toBeInTheDocument()
  })
})
