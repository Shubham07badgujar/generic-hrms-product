/**
 * The setup wizard.
 *
 * What it must get right is not layout. It is that the SERVER decides: which
 * steps exist, which are done, and whether finishing is allowed. The page
 * renders that verdict and re-reads it rather than tracking progress of its
 * own — so these tests drive it from the payload and assert what the
 * administrator is told to do next.
 *
 * The refetch matters more than it looks. An administrator gets here by coming
 * BACK from the screen where they just created a department, so a cached
 * answer would show finished work as outstanding and the wizard would look
 * broken at exactly the moment it should feel like progress.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const state = vi.hoisted(() => ({
  steps: [] as Array<Record<string, unknown>>,
  canFinish: false,
  finishRejects: false,
  gets: 0,
  posts: [] as string[],
  reloaded: 0,
  navigated: [] as string[],
}))

vi.mock('@/app/AuthProvider', () => ({
  useAuth: () => ({
    reloadPermissions: vi.fn(async () => {
      state.reloaded += 1
    }),
  }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => (to: string) => state.navigated.push(to),
  }
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async () => {
      state.gets += 1
      const steps = state.steps
      return {
        status: 'pending_setup',
        in_setup: true,
        steps,
        completed: steps.filter((s) => s.complete).length,
        total: steps.length,
        blocking: steps.filter((s) => s.required && !s.complete).map((s) => s.key),
        can_finish: state.canFinish,
      }
    }),
    apiPost: vi.fn(async (url: string) => {
      state.posts.push(url)
      if (state.finishRejects) {
        throw new actual.ApiError(422, {
          error: {
            code: 'business_rule',
            message: 'Departments and Locations are still required.',
          },
        })
      }
      return { detail: 'ok' }
    }),
  }
})

import { SetupPage } from '@/features/organisation/SetupPage'
import { ToastProvider } from '@/components/ui/Toast'

function step(key: string, title: string, complete: boolean, required = true) {
  return { key, title, required, route: `/settings/${key}`, detail: '', complete }
}

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <SetupPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state.steps = [
    step('company_profile', 'Company profile', true),
    step('departments', 'Departments', false),
    step('locations', 'Locations', false),
    step('payroll_compliance', 'Payroll and compliance', false, false),
  ]
  state.canFinish = false
  state.finishRejects = false
  state.gets = 0
  state.posts = []
  state.reloaded = 0
  state.navigated = []
})

describe('the checklist', () => {
  it('shows progress from the server, not from a count it keeps', async () => {
    renderWizard()

    expect(await screen.findByText('1 of 4 complete')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')
  })

  it('names one next step, and it is the first unfinished required one', async () => {
    renderWizard()

    const next = await screen.findByText('next')
    const row = next.closest('li')!

    expect(within(row).getByText('Departments')).toBeInTheDocument()
    // Exactly one: a wizard that marked three steps "next" would be a list
    // again, which is what this replaced.
    expect(screen.getAllByText('next')).toHaveLength(1)
  })

  it('marks optional steps as optional', async () => {
    renderWizard()

    const optional = await screen.findByText('optional')
    expect(within(optional.closest('li')!).getByText('Payroll and compliance')).toBeInTheDocument()
  })

  it('sends each step to the screen that actually does it', async () => {
    renderWizard()

    const row = (await screen.findByText('Departments')).closest('li')!
    expect(within(row).getByRole('link')).toHaveAttribute('href', '/settings/departments')
  })

  it('offers Review rather than Set up for work already done', async () => {
    renderWizard()

    const done = (await screen.findByText('Company profile')).closest('li')!
    expect(within(done).getByRole('link')).toHaveTextContent('Review')
  })
})

describe('finishing', () => {
  it('is refused while a required step is outstanding', async () => {
    renderWizard()

    const button = await screen.findByRole('button', { name: /finish setup/i })
    expect(button).toBeDisabled()
    // And says which ones, so the administrator does not have to hunt.
    expect(screen.getByText(/Still required: Departments, Locations\./)).toBeInTheDocument()
  })

  it('is allowed once the server says so, even with optional steps left', async () => {
    state.steps = [
      step('company_profile', 'Company profile', true),
      step('departments', 'Departments', true),
      step('payroll_compliance', 'Payroll and compliance', false, false),
    ]
    state.canFinish = true
    renderWizard()

    const button = await screen.findByRole('button', { name: /finish setup/i })
    expect(button).toBeEnabled()

    await userEvent.click(button)

    await waitFor(() => expect(state.posts).toContain('/org/setup/finish/'))
  })

  it('re-reads the session, or the guard sends them straight back', async () => {
    // The organization's status lives in the permission snapshot and the
    // RequireAuth chain reads it. Finishing without re-reading would bounce
    // the administrator back to the checklist they just completed.
    state.canFinish = true
    renderWizard()

    await userEvent.click(await screen.findByRole('button', { name: /finish setup/i }))

    await waitFor(() => expect(state.reloaded).toBe(1))
    expect(state.navigated).toEqual(['/'])
  })

  it('shows the server refusal rather than a generic failure', async () => {
    state.canFinish = true
    state.finishRejects = true
    renderWizard()

    await userEvent.click(await screen.findByRole('button', { name: /finish setup/i }))

    expect(
      await screen.findByText('Departments and Locations are still required.'),
    ).toBeInTheDocument()
    // Nothing was navigated away from: the administrator stays where the work is.
    expect(state.navigated).toEqual([])
  })
})
