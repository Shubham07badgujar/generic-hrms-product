/**
 * The handbook gate and acknowledgement.
 *
 * Three claims: while `handbook_acknowledgement_pending` is up, RequireAuth
 * sends every route to /handbook; the acknowledge button stays disabled until
 * the confirmation box is ticked; and acknowledging completes the employee's
 * own checklist item through the ordinary onboarding-items endpoint, then
 * refreshes the user so the guard lets them through.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const auth = vi.hoisted(() => ({
  handbookPending: true,
  reloadUser: vi.fn(async () => undefined),
}))
const calls = vi.hoisted(() => ({
  posts: [] as Array<{ url: string; body: unknown }>,
  gets: [] as string[],
}))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    useAuth: () => ({
      status: 'authenticated',
      mustChangePassword: false,
      user: {
        email: 'nisha@example.test',
        onboarding_pending: true,
        handbook_acknowledgement_pending: auth.handbookPending,
      },
      logout: vi.fn(),
      reloadUser: auth.reloadUser,
      permissions: new Permissions(snapshotForRole('employee')),
    }),
    usePermissions: () => new Permissions(snapshotForRole('employee')),
  }
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      calls.gets.push(url)
      return { data: [{ id: 'item-ack-1', title: 'Acknowledge the employee handbook' }], meta: {} }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      calls.posts.push({ url, body })
      return { detail: 'ok' }
    }),
  }
})

import { RequireAuth } from '@/app/guards'
import { HandbookPage } from '@/features/employees/HandbookPage'
import { ToastProvider } from '@/components/ui/Toast'

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/handbook"
              element={
                <RequireAuth>
                  <HandbookPage />
                </RequireAuth>
              }
            />
            <Route
              path="/*"
              element={
                <RequireAuth>
                  <div>THE APP</div>
                </RequireAuth>
              }
            />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  calls.posts.length = 0
  calls.gets.length = 0
  auth.handbookPending = true
  auth.reloadUser.mockClear()
})

describe('the handbook gate', () => {
  it('sends a flagged user to the handbook from anywhere — before onboarding', async () => {
    renderAt('/me')

    expect(
      await screen.findByRole('heading', { name: /employee handbook/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText('THE APP')).not.toBeInTheDocument()
  })

  it('does not trap a user who has already acknowledged', async () => {
    auth.handbookPending = false
    renderAt('/handbook')

    // Reference mode: content plus a way back, no acknowledgement demanded.
    expect(
      await screen.findByRole('heading', { name: /employee handbook/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to the hrms/i })).toBeInTheDocument()
  })
})

describe('the acknowledgement', () => {
  it('stays disabled until the confirmation is ticked, then completes the item', async () => {
    const user = userEvent.setup()
    renderAt('/handbook')

    const button = await screen.findByRole('button', { name: /acknowledge and continue/i })
    expect(button).toBeDisabled()

    await user.click(screen.getByRole('checkbox'))
    expect(button).toBeEnabled()

    await user.click(button)
    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/onboarding-items/item-ack-1/complete/')
    await waitFor(() => expect(auth.reloadUser).toHaveBeenCalled())
  })
})
