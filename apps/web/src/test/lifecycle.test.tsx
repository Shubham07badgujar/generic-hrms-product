/**
 * Employee lifecycle UI.
 *
 * The recurring assertion: a section the API WITHHELD must not render as an
 * empty list. The backend omits a section the caller may not read, and an
 * empty table in that case tells the user something false — that there is
 * nothing there. Everything else here checks that the UI offers only the
 * actions the server would accept.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
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

import {
  AssetsSection,
  CompanyAccountSection,
  DocumentsSection,
  LettersSection,
  NotPermitted,
  OnboardingSection,
  ProbationSection,
} from '@/features/employees/ProfileSections'
import { renderWithProviders } from './helpers'
import type {
  AssetAllocation,
  EmployeeDetail,
  EmployeeDocument,
  EmployeeLetter,
  EmployeeOnboarding,
  OnboardingItem,
  ProbationReview,
} from '@/lib/types'

function asRole(role: string) {
  state.role = role
}

beforeEach(() => asRole('hr_head'))

/* ---------------------------------------------------------------- fixtures */

const EMPLOYEE = {
  id: 'employee-1',
  employee_code: 'EMP00042',
  full_name: 'Priya Nair',
  work_email: 'priya@example.test',
  department: 'dept-1',
  department_name: 'Medical',
  designation: null,
  designation_title: 'Therapist',
  reporting_manager: null,
  reporting_manager_name: 'Meera Kulkarni',
  employment_type: 'full_time',
  date_of_joining: '2026-01-05',
  status: 'on_probation',
  roles: ['therapist'],
  first_name: 'Priya',
  middle_name: '',
  last_name: 'Nair',
  personal_email: '',
  phone: '',
  date_of_birth: null,
  gender: 'F',
  location: null,
  level: null,
  team: null,
  probation_start_date: '2026-01-05',
  probation_end_date: '2026-07-05',
  confirmation_date: null,
  probation_status: 'active',
  date_of_exit: null,
  notice_period_days: 30,
  pan: 'ABCxxxx1F',
  aadhaar: 'XXXX XXXX 9012',
  bank_account_number: 'XXXXXX7890',
  bank_ifsc: 'HDFC0001',
  bank_name: 'HDFC',
  uan: '',
  esic_number: '',
} as unknown as EmployeeDetail

function item(overrides: Partial<OnboardingItem> = {}): OnboardingItem {
  return {
    id: 'item-1',
    title: 'Collect signed employment agreement',
    description: '',
    kind: 'task',
    owner: 'hr',
    assigned_to: null,
    assigned_to_name: null,
    document_type: null,
    document_type_name: null,
    document: null,
    is_mandatory: true,
    due_date: '2026-01-05',
    order: 10,
    status: 'pending',
    completed_at: null,
    completed_by: null,
    completed_by_email: null,
    notes: '',
    is_overdue: false,
    is_done: false,
    ...overrides,
  }
}

const ONBOARDING: EmployeeOnboarding = {
  id: 'onboarding-1',
  employee: 'employee-1',
  employee_name: 'Priya Nair',
  employee_code: 'EMP00042',
  department_name: 'Medical',
  template: 'template-1',
  template_name: 'Standard onboarding',
  joining_date: '2026-01-05',
  status: 'in_progress',
  completed_at: null,
  notes: '',
  items: [
    item(),
    item({ id: 'item-2', title: 'Collect PAN card', kind: 'document', document_type: 'dt-1' }),
    item({ id: 'item-3', title: 'Department introduction', owner: 'department_head', is_overdue: true }),
  ],
  completed_count: 0,
  total_count: 3,
  outstanding_mandatory_count: 3,
}

function review(overrides: Partial<ProbationReview> = {}): ProbationReview {
  return {
    id: 'review-1',
    employee: 'employee-1',
    employee_name: 'Priya Nair',
    employee_code: 'EMP00042',
    department_name: 'Medical',
    probation_end_date: '2026-07-05',
    reviewer: null,
    reviewer_name: 'Meera Kulkarni',
    reviewed_at: null,
    performance_rating: null,
    reliability_rating: null,
    role_specific_rating: null,
    strengths: '',
    areas_for_improvement: '',
    recommendation: 'pending',
    reviewer_notes: '',
    decision: 'pending',
    decided_by: null,
    decided_by_email: null,
    decided_at: null,
    rationale: '',
    extended_to: null,
    confirmation_letter: null,
    is_decided: false,
    notified_30d: true,
    notified_7d: false,
    notified_overdue_on: null,
    ...overrides,
  }
}

const DOCUMENT: EmployeeDocument = {
  id: 'document-1',
  employee: 'employee-1',
  employee_name: 'Priya Nair',
  employee_code: 'EMP05000',
  department_name: 'Medical',
  document_type: 'dt-1',
  document_type_name: 'PAN card',
  category: 'identity',
  original_filename: 'pan.pdf',
  content_type: 'application/pdf',
  size_bytes: 1024,
  has_file: true,
  uploaded_by: 'user-1',
  uploaded_by_email: 'hr@example.test',
  uploaded_at: '2026-01-06T10:00:00Z',
  filed_by_someone_else: true,
  status: 'pending',
  verified_by: null,
  verified_by_email: null,
  verified_at: null,
  rejected_by: null,
  rejected_by_email: null,
  rejected_at: null,
  rejection_reason: '',
  issue_date: null,
  expires_on: null,
  notes: '',
}

const ALLOCATION: AssetAllocation = {
  id: 'allocation-1',
  asset: 'asset-1',
  asset_tag: 'LAP-0001',
  asset_name: 'ThinkPad T14',
  asset_category: 'Laptop',
  employee: 'employee-1',
  employee_name: 'Priya Nair',
  allocated_at: '2026-01-05T09:00:00Z',
  allocated_by: 'user-1',
  allocated_by_email: 'hr@example.test',
  condition_at_allocation: 'new',
  allocation_notes: '',
  expected_return_date: null,
  returned_at: null,
  received_by: null,
  received_by_email: null,
  condition_at_return: '',
  return_notes: '',
  status: 'active',
  write_off_reason: '',
  is_open: true,
}

const LETTER: EmployeeLetter = {
  id: 'letter-1',
  employee: 'employee-1',
  employee_name: 'Priya Nair',
  letter_type: 'appointment',
  template: 'lt-1',
  template_name: 'Standard appointment letter',
  template_version: 1,
  subject: 'Appointment as Therapist',
  status: 'issued',
  generated_by: 'user-1',
  generated_by_email: 'hr@example.test',
  generated_at: '2026-01-05T09:00:00Z',
  issued_at: '2026-01-05T09:00:00Z',
  acknowledged_at: null,
  has_pdf: true,
}

/* ============================================ withheld sections */

describe('A section the API withheld', () => {
  it('says so rather than rendering an empty list', () => {
    renderWithProviders(<NotPermitted what="Documents" />)
    expect(screen.getByText('Documents are not visible to you')).toBeInTheDocument()
    expect(screen.getByText(/not the same as there being nothing here/)).toBeInTheDocument()
  })

  it.each([
    ['documents', <DocumentsSection key="d" employeeId="employee-1" documents={undefined} />],
    ['onboarding', <OnboardingSection key="o" onboarding={undefined} employeeId="employee-1" />],
    ['probation', <ProbationSection key="p" reviews={undefined} employee={EMPLOYEE} />],
    ['assets', <AssetsSection key="a" allocations={undefined} employeeId="employee-1" />],
    ['account', <CompanyAccountSection key="c" account={undefined} employeeId="employee-1" />],
    ['letters', <LettersSection key="l" letters={undefined} employee={EMPLOYEE} />],
  ])('is distinguished from empty for %s', (_name, element) => {
    renderWithProviders(element)
    expect(screen.getByText(/are not visible to you/)).toBeInTheDocument()
  })

  it('renders an empty section differently from a withheld one', () => {
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[]} />)
    expect(screen.getByText('No documents on file yet')).toBeInTheDocument()
    expect(screen.queryByText(/are not visible to you/)).not.toBeInTheDocument()
  })
})

/* ============================================ documents */

describe('Documents', () => {
  it('offers verification to a role that holds it', () => {
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[DOCUMENT]} />)
    expect(screen.getByRole('button', { name: 'Verify' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('withholds verification from someone who may only upload', () => {
    // Self-service grants EMPLOYEE_DOCUMENT CREATE but not EDIT, so an employee
    // can supply a document and never attest to it.
    asRole('employee')
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[DOCUMENT]} />)
    expect(screen.queryByRole('button', { name: 'Verify' })).not.toBeInTheDocument()
  })

  it('exposes a download control but never a storage link', () => {
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[DOCUMENT]} />)
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument()
    // The file travels through an authorising route, so no anchor to storage.
    expect(document.querySelector('a[href*="employee-documents"]')).toBeNull()
  })
})

/* ============================================ onboarding */

describe('Onboarding', () => {
  it('shows progress and flags what is outstanding', () => {
    renderWithProviders(
      <OnboardingSection onboarding={ONBOARDING} employeeId="employee-1" />,
    )
    expect(screen.getByText('0 of 3 done')).toBeInTheDocument()
    expect(screen.getByText('3 mandatory outstanding')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  })

  it('marks an overdue item', () => {
    renderWithProviders(
      <OnboardingSection onboarding={ONBOARDING} employeeId="employee-1" />,
    )
    expect(screen.getByText('Overdue')).toBeInTheDocument()
  })

  it('does not offer to tick off a document item', () => {
    /*
     * The API refuses to complete a DOCUMENT item without the file, so the UI
     * does not offer the shortcut it would reject.
     */
    renderWithProviders(
      <OnboardingSection onboarding={ONBOARDING} employeeId="employee-1" />,
    )
    // Two non-document mandatory items carry the control; the document one does not.
    expect(screen.getAllByRole('button', { name: 'Mark done' })).toHaveLength(2)
  })

  it('explains that no checklist exists rather than showing a blank panel', () => {
    renderWithProviders(<OnboardingSection onboarding={null} employeeId="employee-1" />)
    expect(screen.getByText('No onboarding checklist')).toBeInTheDocument()
  })
})

/* ============================================ probation */

describe('Probation', () => {
  it('offers HR the three decisions', () => {
    renderWithProviders(
      <ProbationSection reviews={[review()]} employee={EMPLOYEE} />,
    )
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Extend' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Terminate' })).toBeInTheDocument()
  })

  it('lets a manager assess but never decide', () => {
    // The same split recruitment draws: recommend, do not decide.
    asRole('medical_director')
    renderWithProviders(<ProbationSection reviews={[review()]} employee={EMPLOYEE} />)

    expect(screen.getByRole('button', { name: 'Record assessment' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Terminate' })).not.toBeInTheDocument()
    expect(screen.getByText('Your assessment is advisory')).toBeInTheDocument()
  })

  it('says a due probation still needs a human decision', () => {
    renderWithProviders(
      <ProbationSection
        reviews={[review()]}
        employee={{ ...EMPLOYEE, probation_status: 'due' }}
      />,
    )
    expect(screen.getByText('A decision is due')).toBeInTheDocument()
    expect(screen.getByText(/does not confirm anyone automatically/)).toBeInTheDocument()
  })

  it('requires a rationale to terminate', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ProbationSection reviews={[review()]} employee={EMPLOYEE} />)

    await user.click(screen.getByRole('button', { name: 'Terminate' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName('Terminate during probation')
    expect(
      screen.getByRole('button', { name: 'Terminate employment' }),
    ).toBeDisabled()
  })

  it('shows a decided review as history, not as an open action', () => {
    renderWithProviders(
      <ProbationSection
        reviews={[
          review({
            decision: 'confirm',
            is_decided: true,
            decided_at: '2026-07-06T10:00:00Z',
            decided_by_email: 'hr@example.test',
          }),
        ]}
        employee={{ ...EMPLOYEE, probation_status: 'confirmed' }}
      />,
    )
    expect(screen.getByText('Review history')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })
})

/* ============================================ assets */

describe('Assets', () => {
  it('separates what is held now from what came back', () => {
    renderWithProviders(
      <AssetsSection
        allocations={[
          ALLOCATION,
          {
            ...ALLOCATION,
            id: 'allocation-2',
            status: 'returned',
            returned_at: '2026-02-01T09:00:00Z',
            condition_at_return: 'good',
            is_open: false,
          },
        ]}
        employeeId="employee-1"
      />,
    )
    expect(screen.getByText('Currently assigned')).toBeInTheDocument()
    expect(screen.getByText('History')).toBeInTheDocument()
  })

  it('offers return to HR and write-off only with the heavier grant', () => {
    renderWithProviders(
      <AssetsSection allocations={[ALLOCATION]} employeeId="employee-1" />,
    )
    expect(screen.getByRole('button', { name: 'Record return' })).toBeInTheDocument()
    // HR Head holds ASSET_ALLOCATION/DELETE in the fixture matrix.
    expect(screen.getByRole('button', { name: 'Write off' })).toBeInTheDocument()
  })

  it('shows an employee their allocations without any controls', () => {
    asRole('employee')
    renderWithProviders(
      <AssetsSection allocations={[ALLOCATION]} employeeId="employee-1" />,
    )
    expect(screen.getByText(/LAP-0001/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record return' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Write off' })).not.toBeInTheDocument()
  })
})

/* ============================================ company account */

describe('The company account panel', () => {
  it('states plainly that credentials live outside the system', () => {
    renderWithProviders(
      <CompanyAccountSection account={null} employeeId="employee-1" />,
    )
    expect(
      screen.getByText('Credentials are handled outside this system'),
    ).toBeInTheDocument()
    expect(screen.getByText(/stores no passwords/)).toBeInTheDocument()
  })

  it('offers no password field when recording an account', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <CompanyAccountSection account={null} employeeId="employee-1" />,
    )

    await user.click(screen.getByRole('button', { name: 'Record account' }))

    expect(screen.getByText('Never enter a password here')).toBeInTheDocument()
    expect(document.querySelector('input[type="password"]')).toBeNull()
    for (const label of [/password/i, /secret/i, /credential/i]) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    }
  })
})

/* ============================================ letters */

describe('Letters', () => {
  it('lists an issued letter with its template version', () => {
    renderWithProviders(<LettersSection letters={[LETTER]} employee={EMPLOYEE} />)
    expect(screen.getByText('Appointment letter')).toBeInTheDocument()
    expect(screen.getByText(/Standard appointment letter v1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download PDF' })).toBeInTheDocument()
  })

  it('disables the confirmation letter until HR has confirmed', async () => {
    const user = userEvent.setup()
    renderWithProviders(<LettersSection letters={[]} employee={EMPLOYEE} />)

    await user.click(screen.getByRole('button', { name: 'Generate letter' }))

    const option = screen.getByRole('option', { name: 'Confirmation letter' })
    expect(option).toBeDisabled()
    expect(screen.getByText(/only after HR records a probation confirmation/)).toBeInTheDocument()
  })

  it('enables it once the employee is confirmed', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <LettersSection
        letters={[]}
        employee={{ ...EMPLOYEE, probation_status: 'confirmed' }}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Generate letter' }))
    expect(screen.getByRole('option', { name: 'Confirmation letter' })).toBeEnabled()
  })
})
