/**
 * The reopened application must read as workable.
 *
 * `StageActions` decides what to render from two server-supplied facts: the
 * application's `status` and its `allowed_decisions`. Before the backend fix an
 * override returned `status: 'active'` with `allowed_decisions: []`, and this
 * component correctly rendered "no decisions configured" — a candidate who was
 * reopened on paper and stuck in practice.
 *
 * These tests pin both halves: the closed state stays closed, and the reopened
 * state offers real controls to the role entitled to use them.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

/*
 * Permissions normally arrive from AuthProvider, which bootstraps over HTTP.
 * These are rendering questions, not session questions, so the module is
 * replaced with one that reports whichever role the test names. `vi.hoisted`
 * gives the hoisted mock factory something mutable to read.
 */
const state = vi.hoisted(() => ({ role: 'hr_head' as string }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

import { StageActions } from '@/features/recruitment/StageActions'
import { OverrideAction } from '@/features/recruitment/OverrideDialog'
import { renderWithProviders, snapshotForRole } from './helpers'
import type { Application, ApplicationStatus, Decision } from '@/lib/types'

void snapshotForRole

function asRole(role: keyof typeof import('./helpers').ROLE_FIXTURES) {
  state.role = role as string
}

function application(overrides: Partial<Application> = {}): Application {
  return {
    id: 'application-1',
    candidate: 'candidate-1',
    candidate_name: 'Divya Kamat',
    job_opening: 'job-1',
    job_title: 'Therapist',
    department_name: 'Medical',
    current_stage: 'stage-60',
    stage_name: 'HR Head final decision',
    stage_kind: 'hr_final_decision',
    allowed_decisions: ['select', 'reject'] as Decision[],
    status: 'active' as ApplicationStatus,
    is_verified: true,
    form_answers: {},
    stage_responsible_role: 'hr_head',
    stage_responsible_role_name: 'HR Head',
    candidate_email: 'divya@example.test',
    slot_invite: null,
    stage_interview_scheduled: false,
    stage_feedback_submitted: false,
    conversion_defaults: null,
    applied_at: '2026-08-01T09:00:00Z',
    ...overrides,
  }
}

/** The shape the API returned BEFORE the fix: active, but nothing to do. */
const STUCK = application({
  current_stage: 'stage-95',
  stage_name: 'Rejected',
  stage_kind: 'terminal',
  allowed_decisions: [],
})

/** The shape it returns after the fix. */
const REOPENED = application()

const REJECTED = application({
  status: 'rejected',
  current_stage: 'stage-95',
  stage_name: 'Rejected',
  stage_kind: 'terminal',
  allowed_decisions: [],
})

beforeEach(() => asRole('hr_head'))

describe('A rejected application', () => {
  it('offers no decisions while it is closed', () => {
    renderWithProviders(<StageActions application={REJECTED} />)

    expect(screen.getByText('This application is closed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject candidate' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Select candidate' })).not.toBeInTheDocument()
  })
})

describe('A reopened application', () => {
  it('offers HR Head real decisions again', () => {
    renderWithProviders(<StageActions application={REOPENED} />)

    expect(screen.getByRole('button', { name: 'Select candidate' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Reject candidate' })).toBeEnabled()
    expect(screen.queryByText('This application is closed')).not.toBeInTheDocument()
    expect(screen.queryByText('No decisions configured at this stage')).not.toBeInTheDocument()
  })

  it('lets the rejection dialog be opened again', async () => {
    const user = userEvent.setup()
    renderWithProviders(<StageActions application={REOPENED} />)

    await user.click(screen.getByRole('button', { name: 'Reject candidate' }))
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Reject candidate')
  })

  it('still withholds the decisions from a role that does not hold them', () => {
    // Reopening restored the STAGE, not anyone's authority over it.
    asRole('medical_director')
    renderWithProviders(<StageActions application={REOPENED} />)

    expect(screen.queryByRole('button', { name: 'Reject candidate' })).not.toBeInTheDocument()
    // Named, not vague: the workflow assigns this stage to HR Head.
    expect(screen.getByText(/actioned by HR Head/)).toBeInTheDocument()
  })
})

describe('The pre-fix stuck state', () => {
  it('is recognisable as unworkable, which is why the backend must not produce it', () => {
    renderWithProviders(<StageActions application={STUCK} />)

    // Active, yet nothing to do — the exact defect. Pinned here so a
    // regression in the API is legible rather than mysterious.
    expect(screen.getByText('No decisions configured at this stage')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Select candidate' })).not.toBeInTheDocument()
  })
})

describe('The override dialog', () => {
  it('tells the Admin that reopening moves the candidate to an actionable stage', async () => {
    asRole('admin')
    const user = userEvent.setup()
    renderWithProviders(<OverrideAction application={REJECTED} />)

    await user.click(screen.getByRole('button', { name: /Administrative override/ }))

    expect(screen.getByText('The candidate returns to an actionable stage')).toBeInTheDocument()
    expect(screen.getByText('This is an exceptional action')).toBeInTheDocument()
  })

  it('drops the reopening notice when the override closes instead', async () => {
    asRole('admin')
    const user = userEvent.setup()
    renderWithProviders(<OverrideAction application={REOPENED} />)

    await user.click(screen.getByRole('button', { name: /Administrative override/ }))
    // Active → rejected moves the application the other way; nothing is reopened.
    expect(
      screen.queryByText('The candidate returns to an actionable stage'),
    ).not.toBeInTheDocument()
  })

  it('is not offered to HR Head', () => {
    asRole('hr_head')
    renderWithProviders(<OverrideAction application={REJECTED} />)

    expect(
      screen.queryByRole('button', { name: /Administrative override/ }),
    ).not.toBeInTheDocument()
  })
})

describe('The stage’s responsible role', () => {
  // The workflow names ONE role per stage; the engine refuses anyone else.
  // The panel must not offer a button that click will always fail.
  const AT_APPLICATION = application({
    current_stage: 'stage-10',
    stage_name: 'Application received',
    stage_kind: 'application',
    allowed_decisions: ['pass'],
    stage_responsible_role: 'recruiter',
    stage_responsible_role_name: 'Recruiter',
  })

  it('offers Pass to the Recruiter, who the workflow says acts here', () => {
    asRole('recruiter')
    renderWithProviders(<StageActions application={AT_APPLICATION} />)
    expect(screen.getByRole('button', { name: /pass/i })).toBeEnabled()
  })

  it('tells the HR Head who acts here instead of a button that would be refused', () => {
    asRole('hr_head')
    renderWithProviders(<StageActions application={AT_APPLICATION} />)
    expect(screen.queryByRole('button', { name: /pass/i })).not.toBeInTheDocument()
    expect(screen.getByText(/actioned by Recruiter/)).toBeInTheDocument()
  })
})

describe('An interview round held by the Recruiter', () => {
  const ROUND_1 = application({
    current_stage: 'stage-30',
    stage_name: 'Round 1',
    stage_kind: 'interview',
    allowed_decisions: ['pass', 'recommend_reject'],
    stage_responsible_role: 'recruiter',
    stage_responsible_role_name: 'Recruiter',
    stage_interview_scheduled: true,
    stage_feedback_submitted: true,
  })

  it('offers Pass to the recruiter, authorised as the interviewer’s verdict', () => {
    asRole('recruiter')
    renderWithProviders(<StageActions application={ROUND_1} />)
    expect(screen.getByRole('button', { name: /pass/i })).toBeEnabled()
  })

  it('withholds Pass until the round is actually scheduled', () => {
    asRole('recruiter')
    renderWithProviders(
      <StageActions application={{ ...ROUND_1, stage_interview_scheduled: false }} />,
    )
    expect(screen.queryByRole('button', { name: /pass/i })).not.toBeInTheDocument()
    expect(screen.getByText(/Schedule this round first/)).toBeInTheDocument()
  })

  it('withholds Pass until the interviewer has submitted their feedback', () => {
    asRole('recruiter')
    renderWithProviders(
      <StageActions application={{ ...ROUND_1, stage_feedback_submitted: false }} />,
    )
    expect(screen.queryByRole('button', { name: /pass/i })).not.toBeInTheDocument()
    expect(screen.getByText(/Waiting for the interviewer/)).toBeInTheDocument()
  })

  it('does not offer it to a role the workflow did not put in the chair', () => {
    asRole('hr_head')
    renderWithProviders(<StageActions application={ROUND_1} />)
    expect(screen.queryByRole('button', { name: /pass/i })).not.toBeInTheDocument()
    expect(screen.getByText(/actioned by Recruiter/)).toBeInTheDocument()
  })
})
