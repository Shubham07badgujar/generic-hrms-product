/**
 * Removing a former employee from the profile page.
 *
 * The action exists for Admin / HR Head (EMPLOYEE/DELETE) and only on an
 * exited or terminated record; it asks for a reason and sends DELETE with it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'admin' as string, status: 'exited' as string }))
const deletes = vi.hoisted(() => [] as Array<{ url: string; data?: unknown }>)

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const employee = () => ({
  id: 'e-1', employee_code: 'EMP01033', full_name: 'Adnan Qureshi', first_name: 'Adnan', middle_name: '', last_name: 'Qureshi',
  status: state.status, department: 'dep-med', department_name: 'Medical', designation_title: 'Therapist', roles: ['employee'],
  personal_email: '', work_email: 'adnan@example.test', phone: '', date_of_birth: null, gender: '', location: null, level: null, team: null,
  probation_start_date: null, probation_end_date: null, confirmation_date: null, probation_status: 'not_applicable',
  date_of_exit: '2026-08-01', notice_period_days: null, pan: '', aadhaar: '', bank_account_number: '', bank_ifsc: '', bank_name: '',
  uan: '', esic_number: '', date_of_joining: '2025-01-01', employment_type: 'full_time', reporting_manager: null, reporting_manager_name: null,
  designation: null, user: 'u-1', email: 'adnan@example.test',
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/employees/e-1/profile/') return { employee: employee(), allowed_status_transitions: [], documents: [] }
      if (url === '/me/employee/' || url.endsWith('/me/')) return employee()
      return { data: [], meta: { next: null, previous: null } }
    }),
    http: {
      ...actual.http,
      delete: vi.fn(async (url: string, config?: { data?: unknown }) => {
        deletes.push({ url, data: config?.data })
        return { data: undefined }
      }),
    },
  }
})

import { EmployeeProfileView } from '@/features/employees/EmployeeProfilePage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  state.role = 'admin'
  state.status = 'exited'
  deletes.length = 0
})

describe('Remove from system', () => {
  it('lets Admin remove an exited employee with a reason', async () => {
    const user = userEvent.setup()
    renderWithProviders(<EmployeeProfileView employeeId="e-1" />)
    await user.click(await screen.findByRole('button', { name: /remove from system/i }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByLabelText(/reason/i))
    await user.paste('Left the organisation in August; records retained.')
    await user.click(dialog.getByRole('button', { name: /remove employee/i }))
    await waitFor(() => expect(deletes).toHaveLength(1))
    expect(deletes[0]).toMatchObject({ url: '/employees/e-1/', data: { reason: 'Left the organisation in August; records retained.' } })
  })

  it('is offered to Admin even on a working employee — the master key', async () => {
    state.status = 'confirmed'
    renderWithProviders(<EmployeeProfileView employeeId="e-1" />)
    await screen.findAllByText(/Adnan Qureshi/)
    expect(screen.getByRole('button', { name: /remove from system/i })).toBeInTheDocument()
  })

  it('is not offered to a role without EMPLOYEE/DELETE', async () => {
    state.role = 'recruiter'
    renderWithProviders(<EmployeeProfileView employeeId="e-1" />)
    await screen.findAllByText(/Adnan Qureshi/)
    expect(screen.queryByRole('button', { name: /remove from system/i })).not.toBeInTheDocument()
  })
})
