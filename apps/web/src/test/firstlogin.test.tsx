/**
 * The forced first-login password change.
 *
 * Two claims: while `must_change_password` is up, RequireAuth sends every
 * route to /change-password; and the page posts current + new to the API,
 * refuses a mismatch locally, and signs the user out on success (the server
 * has revoked every session, so pretending otherwise would just produce a
 * 401 on the next click).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { fillIn } from './helpers'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const auth = vi.hoisted(() => ({
  status: 'authenticated' as 'authenticated' | 'anonymous' | 'booting',
  mustChangePassword: true,
  logout: vi.fn(async () => undefined),
}))
const calls = vi.hoisted(() => ({ posts: [] as Array<{ url: string; body: unknown }> }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    useAuth: () => ({
      status: auth.status,
      mustChangePassword: auth.mustChangePassword,
      user: { email: 'nisha@example.test' },
      logout: auth.logout,
      permissions: new Permissions(snapshotForRole('employee')),
    }),
    usePermissions: () => new Permissions(snapshotForRole('employee')),
  }
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiPost: vi.fn(async (url: string, body: unknown) => {
      calls.posts.push({ url, body })
      return { detail: 'ok' }
    }),
  }
})

import { RequireAuth } from '@/app/guards'
import { ChangePasswordPage } from '@/features/auth/ChangePasswordPage'
import { ToastProvider } from '@/components/ui/Toast'

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/change-password"
              element={
                <RequireAuth>
                  <ChangePasswordPage />
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
  auth.mustChangePassword = true
  auth.status = 'authenticated'
  auth.logout.mockClear()
})

describe('the first-login gate', () => {
  it('sends a flagged user to the change screen from anywhere', async () => {
    renderAt('/employees')

    expect(await screen.findByRole('heading', { name: /choose your password/i })).toBeInTheDocument()
    expect(screen.queryByText('THE APP')).not.toBeInTheDocument()
  })

  it('lets an unflagged user through', async () => {
    auth.mustChangePassword = false
    renderAt('/employees')

    expect(await screen.findByText('THE APP')).toBeInTheDocument()
  })
})

describe('the change-password page', () => {
  it('will not submit while the two new passwords differ', async () => {
    const user = userEvent.setup()
    renderAt('/change-password')

    await fillIn(user, await screen.findByLabelText(/temporary password/i), 'Temp-Secret-1234')
    await fillIn(user, screen.getByLabelText(/^new password/i), 'Correct-Horse-Battery-9')
    await fillIn(user, screen.getByLabelText(/confirm new password/i), 'Correct-Horse-Battery-8')

    expect(screen.getByText(/do not match/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /set password and continue/i })).toBeDisabled()
  })

  it('posts current and new, then signs out', async () => {
    const user = userEvent.setup()
    renderAt('/change-password')

    await fillIn(user, await screen.findByLabelText(/temporary password/i), 'Temp-Secret-1234')
    await fillIn(user, screen.getByLabelText(/^new password/i), 'Correct-Horse-Battery-9')
    await fillIn(user, screen.getByLabelText(/confirm new password/i), 'Correct-Horse-Battery-9')
    await user.click(screen.getByRole('button', { name: /set password and continue/i }))

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/auth/change-password/')
    expect(calls.posts[0]!.body).toEqual({
      current_password: 'Temp-Secret-1234',
      new_password: 'Correct-Horse-Battery-9',
    })
    await waitFor(() => expect(auth.logout).toHaveBeenCalled())
  })

  it('never sends the passwords anywhere but the change endpoint', async () => {
    const user = userEvent.setup()
    renderAt('/change-password')

    await fillIn(user, await screen.findByLabelText(/temporary password/i), 'Temp-Secret-1234')
    await fillIn(user, screen.getByLabelText(/^new password/i), 'Correct-Horse-Battery-9')
    await fillIn(user, screen.getByLabelText(/confirm new password/i), 'Correct-Horse-Battery-9')
    await user.click(screen.getByRole('button', { name: /set password and continue/i }))

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts.every((c) => c.url === '/auth/change-password/')).toBe(true)
  })
})
