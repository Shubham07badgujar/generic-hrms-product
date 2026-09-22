/**
 * The customer's side of support access: they decide, and the page shows it.
 *
 * The decision is enforced by the server (ORG_SETTINGS/EDIT, and the grant's
 * own state machine); these tests cover what the page offers and to whom.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '@/components/ui/Toast'

const state = vi.hoisted(() => ({
  role: 'admin' as 'admin' | 'ceo',
  grants: [] as unknown[],
  posts: [] as string[],
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async () => state.grants),
    apiPost: vi.fn(async (url: string) => {
      state.posts.push(url)
      return {}
    }),
  }
})

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  const permissions = () => new Permissions(snapshotForRole(state.role))
  return { usePermissions: permissions, useAuth: () => ({ permissions: permissions() }) }
})

import { SupportAccessPage } from '@/features/organisation/SupportAccessPage'

function grant(overrides: Record<string, unknown>) {
  return {
    id: 'g-1',
    organization_slug: 'northwind',
    organization_name: 'Northwind',
    requested_by_email: 'ops@platform.example',
    reason: 'Customer reports leave balances are wrong since the change.',
    status: 'requested',
    decided_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: '2026-09-22T08:00:00Z',
    usable: false,
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <SupportAccessPage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state.role = 'admin'
  state.grants = []
  state.posts = []
})

describe('support access, from the customer side', () => {
  it('says what an approval shows before anyone decides', async () => {
    renderPage()
    expect(await screen.findByText('No requests')).toBeInTheDocument()
    expect(screen.getByText(/configuration only/)).toBeInTheDocument()
    expect(screen.getByText(/never shows/)).toBeInTheDocument()
  })

  it('lets the administrator approve a request, naming who asked and why', async () => {
    state.grants = [grant({})]
    renderPage()

    expect(await screen.findByText('ops@platform.example')).toBeInTheDocument()
    expect(screen.getByText(/leave balances are wrong/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve for 24 hours' }))

    await waitFor(() => expect(state.posts).toEqual(['/org/support-grants/g-1/approve/']))
  })

  it('lets the administrator end live access early', async () => {
    state.grants = [
      grant({ status: 'approved', usable: true, expires_at: '2026-09-23T08:00:00Z' }),
    ]
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'End access now' }))
    await waitFor(() => expect(state.posts).toEqual(['/org/support-grants/g-1/revoke/']))
  })

  it('shows an expired approval as expired, with nothing to do', async () => {
    state.grants = [grant({ status: 'approved', usable: false, expires_at: '2026-09-20T08:00:00Z' })]
    renderPage()

    expect(await screen.findByText('Expired')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers no decision to someone who can read settings but not change them', async () => {
    // CEO: organisation-wide VIEW, no EDIT anywhere.
    state.role = 'ceo'
    state.grants = [grant({})]
    renderPage()

    expect(await screen.findByText('ops@platform.example')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
  })
})
