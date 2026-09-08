/**
 * The simple asset module.
 *
 * HR Head: adds assets to the register (name + Asset ID and little else),
 * assigns several at once from the employee profile, returns them there too.
 * Employees: see their own, with no management controls offered.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const posts = vi.hoisted(() => [] as Array<{ url: string; body?: unknown }>)

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const asset = (id: string, tag: string, name: string, status = 'available') => ({
  id, asset_tag: tag, name, category: 'cat-1', category_name: 'General',
  serial_number: id === 'a-1' ? 'SN-100' : '', make: '', model: '',
  purchase_date: null, purchase_cost: null, warranty_expires_on: null, vendor: '',
  location: null, location_name: null, status, condition: 'good', notes: '',
  held_by_name: null,
})

const allocation = {
  id: 'al-1', asset: 'a-9', asset_tag: 'AST-009', asset_name: 'Mobile Phone',
  asset_category: 'General', employee: 'e-1', employee_name: 'Adnan Qureshi',
  allocated_at: '2026-08-01T10:00:00Z', allocated_by: 'u-hr', allocated_by_email: 'hr@x.test',
  condition_at_allocation: 'good', allocation_notes: '', expected_return_date: null,
  returned_at: null, received_by: null, received_by_email: null,
  condition_at_return: '', return_notes: '', status: 'active', write_off_reason: '',
  is_open: true,
}

const employee = () => ({
  id: 'e-1', employee_code: 'EMP01033', full_name: 'Adnan Qureshi', first_name: 'Adnan',
  middle_name: '', last_name: 'Qureshi', status: 'confirmed', department: 'dep-med',
  department_name: 'Medical', designation_title: 'Therapist', roles: ['employee'],
  personal_email: '', work_email: 'adnan@example.test', phone: '', date_of_birth: null,
  gender: '', location: null, level: null, team: null, probation_start_date: null,
  probation_end_date: null, confirmation_date: null, probation_status: 'not_applicable',
  date_of_exit: null, notice_period_days: null, pan: '', aadhaar: '',
  bank_account_number: '', bank_ifsc: '', bank_name: '', uan: '', esic_number: '',
  date_of_joining: '2025-01-01', employment_type: 'full_time', reporting_manager: null,
  reporting_manager_name: null, designation: null, user: 'u-1', email: 'adnan@example.test',
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/employees/e-1/profile/')
        return {
          employee: employee(),
          allowed_status_transitions: [],
          asset_allocations: [allocation],
          onboarding: { status: 'completed', outstanding_mandatory_count: 0, items: [] },
        }
      if (url === '/locations/') return []
      if (url === '/assets/')
        return {
          data: [asset('a-1', 'AST-001', 'Dell Laptop'), asset('a-3', 'AST-003', 'Headphones')],
          meta: { next: null, previous: null },
        }
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async (url: string, body?: unknown) => {
      posts.push({ url, body })
      if (url === '/asset-allocations/bulk/') return [allocation, allocation]
      return asset('a-new', 'AST-100', 'New Asset')
    }),
  }
})

import { AssetsPage } from '@/features/employees/AssetsPage'
import { EmployeeProfileView } from '@/features/employees/EmployeeProfilePage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  state.role = 'hr_head'
  posts.length = 0
})

describe('Asset register', () => {
  it('lets HR add an asset with the simple fields — status is never asked', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: /add asset/i }))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.queryByLabelText(/status/i)).not.toBeInTheDocument()

    await user.type(dialog.getByLabelText(/asset name/i), 'Dell Laptop')
    await user.type(dialog.getByLabelText(/asset id/i), 'AST-100')
    await user.type(dialog.getByLabelText(/serial/i), '5CG777')
    await user.click(dialog.getByRole('button', { name: /add asset/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({
      url: '/assets/',
      body: { asset_tag: 'AST-100', name: 'Dell Laptop', serial_number: '5CG777' },
    })
  })

  it('shows Assigned (not "allocated") and offers Assign only on available rows', async () => {
    renderWithProviders(<AssetsPage />)
    await screen.findByText('AST-001')
    // Both fixtures are available → two Assign buttons, no "Allocated" wording.
    expect(screen.getAllByRole('button', { name: /^assign$/i })).toHaveLength(2)
    expect(screen.queryByText(/allocated/i)).not.toBeInTheDocument()
  })
})

describe('Employee profile assets', () => {
  it('assigns several available assets at once from the profile', async () => {
    const user = userEvent.setup()
    renderWithProviders(<EmployeeProfileView employeeId="e-1" />)

    await user.click(await screen.findByRole('tab', { name: /assets/i }))
    await user.click(await screen.findByRole('button', { name: /assign assets/i }))

    const dialog = within(await screen.findByRole('dialog'))
    await user.click(await dialog.findByLabelText(/Dell Laptop — AST-001/i))
    await user.click(dialog.getByLabelText(/Headphones — AST-003/i))
    await user.click(dialog.getByRole('button', { name: /assign 2 selected assets/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({
      url: '/asset-allocations/bulk/',
      body: { employee: 'e-1', assets: ['a-1', 'a-3'] },
    })
  })

  it('offers employees no assign or return controls on their own assets', async () => {
    state.role = 'employee'
    renderWithProviders(<EmployeeProfileView employeeId="e-1" />)

    await userEvent.setup().click(await screen.findByRole('tab', { name: /assets/i }))
    await screen.findByText(/AST-009/)
    expect(screen.queryByRole('button', { name: /assign assets/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /record return/i })).not.toBeInTheDocument()
  })
})
