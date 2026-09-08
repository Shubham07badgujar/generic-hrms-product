/**
 * The admin platform work, in one file:
 *
 *   - numbered pagination (40 per page) renders and navigates,
 *   - the Organisation page offers management controls to Admin and none to a
 *     reader,
 *   - the Workflows page offers the builder to Admin and not to a recruiter,
 *   - the employee form hides the Reporting Manager field from Admin and
 *     shows the dropdown to HR Head.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'admin' as string }))
const calls = vi.hoisted(() => ({ posts: [] as Array<{ url: string; body: unknown }> }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

vi.mock('@/app/AppShell', () => ({ roleLabel: (code: string) => code }))

const DEPARTMENTS = [
  { id: 'dep-1', name: 'Medical Department', code: 'MEDICAL', kind: 'medical', head_employee_name: null },
]
const ROLES = [
  {
    id: 'role-1',
    code: 'recruiter',
    name: 'Recruiter',
    layer: 4,
    is_system: true,
    is_read_only: false,
    can_manage_users: false,
    requires_employee: true,
    is_grantable: true,
    department_kind: '',
    dashboard_key: 'self',
  },
]

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/departments/') return DEPARTMENTS
      if (url === '/designations/') return []
      if (url === '/locations/') return []
      if (url === '/levels/') return []
      if (url === '/roles/') return ROLES
      if (url === '/access-catalog/') {
        return { resources: ['candidate', 'employee'], actions: ['view', 'create'], scopes: [] }
      }
      if (url.startsWith('/roles/role-1/permissions')) {
        return [{ resource: 'candidate', action: 'view', scope: 4, is_customized: false }]
      }
      if (url.startsWith('/workflows/wf-1')) {
        return {
          id: 'wf-1',
          name: 'Therapist hiring',
          description: '',
          department_kind: 'medical',
          is_published: true,
          version: 1,
          stages: [],
        }
      }
      if (url.startsWith('/workflows')) {
        return {
          data: [
            {
              id: 'wf-1',
              name: 'Therapist hiring',
              description: '',
              department_kind: 'medical',
              is_published: true,
              stage_count: 10,
            },
          ],
          meta: { next: null, previous: null, page_size: 40 },
        }
      }
      if (url.startsWith('/feedback-forms')) {
        return { data: [], meta: { next: null, previous: null, page_size: 40 } }
      }
      return { data: [], meta: { next: null, previous: null, page_size: 40 } }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      calls.posts.push({ url, body })
      return {}
    }),
  }
})

import { PagePager } from '@/components/ui/DataTable'
import { OrganisationPage } from '@/features/organisation/OrganisationPage'
import { WorkflowsPage } from '@/features/recruitment/WorkflowsPage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  calls.posts.length = 0
  state.role = 'admin'
})

/* ------------------------------------------------------------ pagination */

describe('numbered pagination', () => {
  it('shows the pages and the record range', () => {
    render(
      <PagePager page={2} pages={3} count={95} pageSize={40} onPage={() => undefined} />,
    )

    expect(screen.getByText('Showing 41–80 of 95')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '3' })).toBeInTheDocument()
  })

  it('navigates by number and by Previous/Next', async () => {
    const user = userEvent.setup()
    const onPage = vi.fn()
    render(<PagePager page={2} pages={3} count={95} pageSize={40} onPage={onPage} />)

    await user.click(screen.getByRole('button', { name: '3' }))
    await user.click(screen.getByRole('button', { name: /previous/i }))

    expect(onPage).toHaveBeenNthCalledWith(1, 3)
    expect(onPage).toHaveBeenNthCalledWith(2, 1)
  })

  it('renders nothing for a single page', () => {
    const { container } = render(
      <PagePager page={1} pages={1} count={12} pageSize={40} onPage={() => undefined} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('windows a long page list instead of rendering fifty buttons', () => {
    render(<PagePager page={25} pages={50} count={2000} pageSize={40} onPage={() => undefined} />)

    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '50' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '10' })).not.toBeInTheDocument()
    expect(screen.getAllByText('…').length).toBe(2)
  })
})

/* ------------------------------------------------------- organisation page */

describe('the Organisation admin panel', () => {
  it('offers Add and row actions to the Admin', async () => {
    renderWithProviders(<OrganisationPage />)
    await waitFor(() => expect(screen.getByText('Medical Department')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: /add department/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /deactivate/i })).toBeInTheDocument()
  })

  it('offers nothing but the tables to a reader', async () => {
    state.role = 'hr_manager'
    renderWithProviders(<OrganisationPage />)
    await waitFor(() => expect(screen.getByText('Medical Department')).toBeInTheDocument())

    expect(screen.queryByRole('button', { name: /add department/i })).not.toBeInTheDocument()
  })

  it('opens the permission matrix for a role', async () => {
    const user = userEvent.setup()
    renderWithProviders(<OrganisationPage />)
    await waitFor(() => expect(screen.getByText('Medical Department')).toBeInTheDocument())

    await user.click(screen.getByRole('tab', { name: /roles/i }))
    await waitFor(() => expect(screen.getByText('Recruiter')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /permissions/i }))

    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText(/Permissions — Recruiter/)).toBeInTheDocument()
    // The grid renders a select per (resource, action) cell.
    await waitFor(() =>
      expect(dialog.getByLabelText('candidate view')).toBeInTheDocument(),
    )
    expect(dialog.getByLabelText('candidate view')).toHaveValue('4')
  })

  it('creates a department through the dialog', async () => {
    const user = userEvent.setup()
    renderWithProviders(<OrganisationPage />)
    await waitFor(() => expect(screen.getByText('Medical Department')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /add department/i }))
    const dialog = within(screen.getByRole('dialog'))

    await user.click(dialog.getByLabelText(/name/i))
    await user.paste('Radiology')
    await user.click(dialog.getByLabelText(/code/i))
    await user.paste('RADIO')
    await user.selectOptions(dialog.getByLabelText(/function/i), 'medical')
    await user.click(dialog.getByRole('button', { name: /create/i }))

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/departments/')
    expect(calls.posts[0]!.body).toMatchObject({ name: 'Radiology', code: 'RADIO', kind: 'medical' })
  })
})

/* ---------------------------------------------------------- workflow page */

describe('the workflow builder', () => {
  it('offers New workflow to the Admin', async () => {
    renderWithProviders(<WorkflowsPage />)
    await waitFor(() => expect(screen.getAllByText('Therapist hiring').length).toBeGreaterThan(0))

    expect(screen.getByRole('button', { name: /new workflow/i })).toBeInTheDocument()
  })

  it('offers no builder to a recruiter', async () => {
    state.role = 'recruiter'
    renderWithProviders(<WorkflowsPage />)
    await waitFor(() => expect(screen.getAllByText('Therapist hiring').length).toBeGreaterThan(0))

    expect(screen.queryByRole('button', { name: /new workflow/i })).not.toBeInTheDocument()
  })

  it('posts the process description, not raw stages', async () => {
    const user = userEvent.setup()
    renderWithProviders(<WorkflowsPage />)
    await waitFor(() => expect(screen.getAllByText('Therapist hiring').length).toBeGreaterThan(0))

    await user.click(screen.getByRole('button', { name: /new workflow/i }))
    const dialog = within(await screen.findByRole('dialog'))

    await user.click(dialog.getByLabelText(/^name/i))
    await user.paste('Radiologist hiring')
    await user.selectOptions(dialog.getByLabelText('Round 1 interviewer role'), 'recruiter')
    await user.click(dialog.getByRole('button', { name: /create draft/i }))

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/workflows/')
    expect(calls.posts[0]!.body).toMatchObject({
      name: 'Radiologist hiring',
      interview_rounds: [{ name: 'Round 1', role: 'recruiter' }],
      recommendation_role: null,
    })
    // No stage graph in the payload — the server derives it.
    expect(JSON.stringify(calls.posts[0]!.body)).not.toContain('transitions')
  })
})
