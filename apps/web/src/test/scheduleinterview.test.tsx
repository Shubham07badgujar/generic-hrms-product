/**
 * The interviewer picker, against the production data shape.
 *
 * Twice now this picker has come back empty for a round that had a perfectly
 * good interviewer. First it asked the directory for `status=active`, which in
 * a mature organisation matches nobody (everyone is confirmed). Then it asked
 * the directory at all: that list is scoped to what the caller may see and
 * capped by a page size, so an interviewer in another department — or the only
 * holder of a role whose other holders had left — could vanish from it.
 *
 * Eligibility is now the server's answer, from the stage's own role. These
 * tests hold the picker to that: it asks the endpoint, never the directory,
 * and when the pool is empty it repeats the server's explanation instead of
 * leaving HR staring at an empty dropdown.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const calls = vi.hoisted(() => ({
  employees: [] as Array<Record<string, string> | undefined>,
  eligible: [] as Array<Record<string, string> | undefined>,
}))
const pool = vi.hoisted(() => ({
  data: [] as Array<Record<string, string>>,
  problem: '',
}))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const RAVI = {
  id: 'e-ravi',
  employee_code: 'EMP01020',
  full_name: 'Ravi Shah',
  status: 'confirmed',
  department_name: 'Human Resources',
  designation_title: 'Recruiter',
}

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string, config?: { params?: Record<string, string> }) => {
      if (url === '/employees/') {
        // Recorded so the test can prove the picker no longer depends on it.
        calls.employees.push(config?.params)
        return { data: [], meta: { next: null, previous: null, page_size: 40, count: 0, page: 1, pages: 1 } }
      }
      if (url === '/interviews/eligible-interviewers/') {
        calls.eligible.push(config?.params)
        return {
          stage: 's-30',
          stage_name: 'Round 1',
          role_code: 'recruiter',
          role_name: 'Recruiter',
          data: pool.data,
          problem: pool.problem,
        }
      }
      if (url === '/interviews/check-conflict/') return { conflict: false, interview: null }
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async () => ({})),
  }
})

import { ScheduleInterviewDrawer } from '@/features/recruitment/ScheduleInterview'
import { renderWithProviders } from './helpers'
import type { Application, WorkflowStage } from '@/lib/types'

const STAGE: WorkflowStage = {
  id: 's-30', name: 'Round 1', order: 30, kind: 'interview',
  responsible_role: 'role-rec', responsible_role_code: 'recruiter',
  allowed_decisions: ['pass'], requires_interview: true, requires_feedback: false,
  feedback_form: null, is_final_hr_decision: false, is_terminal: false, is_won: false, transitions: [],
}
const APPLICATION = {
  id: 'app-1', candidate: 'c-1', candidate_name: 'Shubham Pramod Badgujar', job_opening: 'job-1',
  job_title: 'Customer Relationship Executive', department_name: 'Human Resources',
  current_stage: 's-30', stage_name: 'Round 1', stage_kind: 'interview', allowed_decisions: ['pass'],
  status: 'active', is_verified: true, applied_at: '2026-08-18T09:00:00Z', form_answers: {},
  stage_responsible_role: 'recruiter', stage_responsible_role_name: 'Recruiter', candidate_email: '',
} as Application

beforeEach(() => {
  state.role = 'hr_head'
  calls.employees.length = 0
  calls.eligible.length = 0
  pool.data = [RAVI]
  pool.problem = ''
})

describe('the interviewer picker', () => {
  it('offers whoever the server says may take this round', async () => {
    renderWithProviders(
      <ScheduleInterviewDrawer open onClose={() => {}} application={APPLICATION} stage={STAGE} />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    const select = (await dialog.findByLabelText(/interviewer/i)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))

    const labels = Array.from(select.options).map((o) => o.textContent ?? '')
    expect(labels.some((l) => /Ravi Shah/.test(l))).toBe(true)

    // Asked the stage, not the directory — the directory is scoped and paged.
    expect(calls.eligible[0]?.stage).toBe('s-30')
    expect(calls.employees).toHaveLength(0)
    expect(dialog.queryByText(/This round has no interviewer/)).not.toBeInTheDocument()
  })

  it('repeats the server explanation when nobody can take the round', async () => {
    pool.data = []
    pool.problem =
      "'Round 1' needs the Recruiter role, and everyone who holds it has left the organisation (Gone Recruiter). Give the role to a current employee, or point this round at a different role."

    renderWithProviders(
      <ScheduleInterviewDrawer open onClose={() => {}} application={APPLICATION} stage={STAGE} />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    expect(await dialog.findByText(/has left the organisation/)).toBeInTheDocument()
    expect(await dialog.findByText(/Give the role to a current employee/)).toBeInTheDocument()
  })
})
