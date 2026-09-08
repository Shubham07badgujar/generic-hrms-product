/**
 * The onboarding form: HR Head verifies nine things — name, work email,
 * phone, department, designation, location, reporting manager, joining date —
 * pre-filled from the candidate, the job and the offer, then the employee and
 * their login are minted in one transaction.
 *
 * Pins the two halves the backend cannot see: the prefill actually reaches
 * the inputs, and what HR edits is what the convert endpoint receives.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const convertMutate = vi.hoisted(() => vi.fn())

vi.mock('@/lib/queries', async () => {
  const actual = await vi.importActual<typeof import('@/lib/queries')>('@/lib/queries')
  return {
    ...actual,
    useConvertToEmployee: () => ({ mutate: convertMutate, isPending: false }),
    useDepartments: () => ({
      data: [
        { id: 'dept-med', name: 'Medical' },
        { id: 'dept-ops', name: 'Operations' },
      ],
    }),
    useDesignations: () => ({
      data: [
        { id: 'desig-doc', title: 'Doctor', department: 'dept-med' },
        { id: 'desig-ops', title: 'Operations Coordinator', department: 'dept-ops' },
        { id: 'desig-any', title: 'Staff Member', department: null },
      ],
    }),
    useLocations: () => ({ data: [{ id: 'loc-1', name: 'Head Office' }] }),
    useCurrentEmployees: () => ({
      rows: [
        {
          id: 'emp-md',
          full_name: 'Vyanketesh Joshi',
          designation_title: 'Medical Director',
          department_name: 'Medical',
        },
      ],
      isLoading: false,
    }),
  }
})

import { ConvertPanel } from '@/features/recruitment/ConvertPanel'
import { renderWithProviders } from './helpers'
import type { Application } from '@/lib/types'

const APPLICATION = {
  id: 'application-1',
  candidate: 'candidate-1',
  candidate_name: 'Asha Kamat',
  job_opening: 'job-1',
  job_title: 'Doctor',
  department_name: 'Medical',
  current_stage: 'stage-70',
  stage_name: 'Offer',
  stage_kind: 'offer',
  allowed_decisions: [],
  status: 'offer_accepted',
  is_verified: true,
  applied_at: '2026-08-01T09:00:00Z',
  form_answers: {},
  stage_responsible_role: 'hr_head',
  stage_responsible_role_name: 'HR Head',
  candidate_email: 'asha@example.test',
  slot_invite: null,
  conversion_defaults: {
    first_name: 'Asha',
    last_name: 'Kamat',
    email: 'asha@example.test',
    personal_email: 'asha@example.test',
    phone: '9000000001',
    department: 'dept-med',
    designation: 'desig-doc',
    location: 'loc-1',
    reporting_manager: 'emp-md',
    date_of_joining: '2026-10-01',
  },
} as unknown as Application

beforeEach(() => {
  state.role = 'hr_head'
  convertMutate.mockReset()
})

async function openTheForm() {
  const user = userEvent.setup()
  renderWithProviders(<ConvertPanel application={APPLICATION} />)
  await user.click(screen.getByRole('button', { name: /start onboarding/i }))
  return user
}

describe('the onboarding form', () => {
  it('opens pre-filled from the candidate, the job and the offer', async () => {
    await openTheForm()

    expect(screen.getByLabelText(/first name/i)).toHaveValue('Asha')
    expect(screen.getByLabelText(/last name/i)).toHaveValue('Kamat')
    // The company email is never assumed from the candidate's address —
    // HR must enter it; the applied-with address lands on Personal Email.
    expect(screen.getByLabelText(/company email/i)).toHaveValue('')
    expect(screen.getByLabelText(/personal email/i)).toHaveValue('asha@example.test')
    expect(screen.getByLabelText(/phone number/i)).toHaveValue('9000000001')
    expect(screen.getByLabelText(/department/i)).toHaveValue('dept-med')
    expect(screen.getByLabelText(/designation/i)).toHaveValue('desig-doc')
    expect(screen.getByLabelText(/^location/i)).toHaveValue('loc-1')
    expect(screen.getByLabelText(/reporting manager/i)).toHaveValue('emp-md')
    expect(screen.getByLabelText(/date of joining/i)).toHaveValue('2026-10-01')
  })

  it('submits what HR verified — including corrections', async () => {
    const user = await openTheForm()

    const email = screen.getByLabelText(/company email/i)
    await user.type(email, 'asha.kamat@example.test')

    await user.click(screen.getByRole('button', { name: /create employee/i }))

    await waitFor(() => expect(convertMutate).toHaveBeenCalledTimes(1))
    expect(convertMutate.mock.calls[0][0]).toMatchObject({
      first_name: 'Asha',
      last_name: 'Kamat',
      email: 'asha.kamat@example.test',
      personal_email: 'asha@example.test',
      phone: '9000000001',
      department: 'dept-med',
      designation: 'desig-doc',
      location: 'loc-1',
      reporting_manager: 'emp-md',
      date_of_joining: '2026-10-01',
    })
  })

  it('only offers designations valid for the chosen department', async () => {
    await openTheForm()

    const options = Array.from(
      (screen.getByLabelText(/designation/i) as HTMLSelectElement).options,
    ).map((option) => option.text)

    expect(options).toContain('Doctor')
    expect(options).toContain('Staff Member') // unassigned titles work anywhere
    expect(options).not.toContain('Operations Coordinator')
  })

  it('is not offered to a role without EMPLOYEE/CREATE', async () => {
    state.role = 'recruiter'
    renderWithProviders(<ConvertPanel application={APPLICATION} />)

    expect(screen.queryByRole('button', { name: /start onboarding/i })).toBeNull()
    expect(screen.getByText(/restricted to HR Head/i)).toBeInTheDocument()
  })
})
