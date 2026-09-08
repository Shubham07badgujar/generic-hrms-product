/**
 * The stepper must render whatever the API says, and nothing it knows in advance.
 *
 * The strongest test here is the last one: a workflow whose stages are entirely
 * invented — no therapist, no office boy, no role this app has ever heard of —
 * renders correctly. If someone later special-cases a pipeline in the frontend,
 * that test is what fails.
 */

import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { WorkflowStepper } from '@/features/recruitment/WorkflowStepper'
import { renderWithProviders } from './helpers'
import type { HiringWorkflow, StageKind, WorkflowStage } from '@/lib/types'

function stage(overrides: Partial<WorkflowStage> & { order: number; name: string }): WorkflowStage {
  return {
    id: `stage-${overrides.order}`,
    kind: 'interview' as StageKind,
    responsible_role: null,
    responsible_role_code: null,
    allowed_decisions: [],
    requires_interview: false,
    requires_feedback: false,
    feedback_form: null,
    is_final_hr_decision: false,
    is_terminal: false,
    is_won: false,
    transitions: [],
    ...overrides,
  }
}

function workflow(stages: WorkflowStage[], name = 'Test pipeline'): HiringWorkflow {
  return {
    id: 'workflow-1',
    name,
    description: '',
    department_kind: '',
    is_published: true,
    version: 1,
    stages,
  }
}

const THERAPIST = workflow(
  [
    stage({ order: 10, name: 'Application received', kind: 'application' }),
    stage({ order: 20, name: 'HR verification', kind: 'hr_verification' }),
    stage({ order: 30, name: 'Clinic Doctor interview', requires_interview: true }),
    stage({ order: 40, name: 'Senior Doctor interview', requires_interview: true }),
    stage({ order: 50, name: 'Medical Director recommendation', kind: 'department_decision' }),
    stage({ order: 60, name: 'HR Head final decision', kind: 'hr_final_decision', is_final_hr_decision: true }),
    stage({ order: 70, name: 'Offer', kind: 'offer' }),
    stage({ order: 80, name: 'Onboarding', kind: 'onboarding' }),
    stage({ order: 90, name: 'Hired', kind: 'terminal', is_terminal: true, is_won: true }),
    stage({ order: 95, name: 'Rejected', kind: 'terminal', is_terminal: true }),
  ],
  'Therapist hiring',
)

const OFFICE_BOY = workflow(
  [
    stage({ order: 10, name: 'Application received', kind: 'application' }),
    stage({ order: 20, name: 'HR verification', kind: 'hr_verification' }),
    stage({ order: 30, name: 'CRE interview', requires_interview: true }),
    stage({ order: 40, name: 'Operations Manager interview', requires_interview: true }),
    stage({ order: 50, name: 'Operational Head recommendation', kind: 'department_decision' }),
    stage({ order: 60, name: 'HR Head final decision', kind: 'hr_final_decision', is_final_hr_decision: true }),
    stage({ order: 90, name: 'Hired', kind: 'terminal', is_terminal: true, is_won: true }),
    stage({ order: 95, name: 'Rejected', kind: 'terminal', is_terminal: true }),
  ],
  'Office Boy hiring',
)

describe('WorkflowStepper', () => {
  it('renders the stages the API returns, in order', () => {
    renderWithProviders(<WorkflowStepper workflow={THERAPIST} />)

    expect(screen.getByText('Clinic Doctor interview')).toBeInTheDocument()
    expect(screen.getByText('Medical Director recommendation')).toBeInTheDocument()
    expect(screen.getByText('HR Head final decision')).toBeInTheDocument()
  })

  it('renders a completely different pipeline through the same component', () => {
    const { unmount } = renderWithProviders(<WorkflowStepper workflow={THERAPIST} />)
    expect(screen.getByText('Clinic Doctor interview')).toBeInTheDocument()
    unmount()

    renderWithProviders(<WorkflowStepper workflow={OFFICE_BOY} />)
    expect(screen.getByText('CRE interview')).toBeInTheDocument()
    expect(screen.getByText('Operational Head recommendation')).toBeInTheDocument()
    // Nothing from the other pipeline leaks through.
    expect(screen.queryByText('Clinic Doctor interview')).not.toBeInTheDocument()
  })

  it('renders a pipeline it has never seen, with no code change', () => {
    // The genuine test of "configuration, not code". None of these stages,
    // roles or job titles exist anywhere in this codebase.
    const invented = workflow(
      [
        stage({ order: 10, name: 'Portfolio submitted', kind: 'application' }),
        stage({ order: 20, name: 'Studio review', requires_interview: true }),
        stage({ order: 30, name: 'Guild assessment', kind: 'department_decision' }),
        stage({
          order: 40,
          name: 'Chancellor sign-off',
          kind: 'hr_final_decision',
          is_final_hr_decision: true,
        }),
        stage({ order: 90, name: 'Appointed', kind: 'terminal', is_terminal: true, is_won: true }),
      ],
      'Guild appointment',
    )

    renderWithProviders(<WorkflowStepper workflow={invented} />)

    expect(screen.getByText('Portfolio submitted')).toBeInTheDocument()
    expect(screen.getByText('Studio review')).toBeInTheDocument()
    expect(screen.getByText('Guild assessment')).toBeInTheDocument()
    expect(screen.getByText('Chancellor sign-off')).toBeInTheDocument()
    expect(screen.getByText('Appointed')).toBeInTheDocument()
  })

  it('marks the current stage and separates the outcomes from the track', () => {
    renderWithProviders(<WorkflowStepper workflow={THERAPIST} currentStageId="stage-30" />)

    expect(screen.getByText('Current')).toBeInTheDocument()
    // Terminal stages appear under "Outcomes", not as steps in the line.
    expect(screen.getByText('Outcomes:')).toBeInTheDocument()
    expect(screen.getByText('Hired')).toBeInTheDocument()
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('drops the "current" marker once the application is closed', () => {
    renderWithProviders(<WorkflowStepper workflow={THERAPIST} currentStageId="stage-30" closed />)
    expect(screen.queryByText('Current')).not.toBeInTheDocument()
  })

  it('a hire completes the whole track, stages beyond the stop point included', () => {
    // Conversion closes the application at the Offer stage, so without the
    // hired flag the later stages sat grey under a candidate the page calls
    // Hired. Every non-terminal dot must render as done (a check mark, so no
    // numbered dots remain).
    const { container } = renderWithProviders(
      <WorkflowStepper workflow={THERAPIST} currentStageId="stage-30" closed hired />,
    )

    const track = THERAPIST.stages.filter((stage) => !stage.is_terminal)
    expect(container.querySelectorAll('ol svg')).toHaveLength(track.length)
    expect(screen.queryByText('Current')).not.toBeInTheDocument()
  })

  it('labels the final HR decision stage wherever it sits', () => {
    renderWithProviders(<WorkflowStepper workflow={OFFICE_BOY} />)
    expect(screen.getByText('HR decision')).toBeInTheDocument()
  })
})
