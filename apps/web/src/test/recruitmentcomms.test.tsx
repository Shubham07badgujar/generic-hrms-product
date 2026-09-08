/**
 * The public application form, the candidate email history, and the
 * job-specific candidate list.
 *
 * The public form talks to the API through its own anonymous axios instance,
 * so it is tested through axios's adapter rather than the app's `apiGet`
 * mock — that IS the point: no session, no bearer token, no redirect.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import axios from 'axios'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const apiCalls = vi.hoisted(() => ({ get: [] as string[], post: [] as Array<[string, unknown]> }))
const notifications = vi.hoisted(() => ({
  rows: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const JOB = {
  id: 'job-1', title: 'Therapist', department_name: 'Medical', workflow_name: 'Therapist hiring',
  status: 'published', application_count: 2, openings_count: 1, department: 'dept-med',
  application_token: 'tok123', application_url: 'https://hrms.example/apply/tok123',
  accepts_applications: true, application_fields: [], resolved_application_fields: [{ key: 'full_name' }],
  external_form_provider: 'hosted', external_form_id: '', external_form_url: '', external_form_error: '',
}
const APPLICATIONS = [
  { id: 'app-1', candidate: 'c-1', candidate_name: 'Nisha Verma', job_opening: 'job-1', job_title: 'Therapist',
    department_name: 'Medical', current_stage: 's-20', stage_name: 'HR verification', stage_kind: 'hr_verification',
    allowed_decisions: [], status: 'active', is_verified: false, applied_at: '2026-08-10T09:00:00Z', form_answers: {}, stage_responsible_role: 'recruiter', stage_responsible_role_name: 'Recruiter', candidate_email: '', slot_invite: null },
  { id: 'app-2', candidate: 'c-2', candidate_name: 'Ravi Kumar', job_opening: 'job-2', job_title: 'Office Boy',
    department_name: 'Operations', current_stage: 's-10', stage_name: 'Application', stage_kind: 'application',
    allowed_decisions: [], status: 'active', is_verified: false, applied_at: '2026-08-11T09:00:00Z', form_answers: {}, stage_responsible_role: 'recruiter', stage_responsible_role_name: 'Recruiter', candidate_email: '', slot_invite: null },
]

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string, config?: { params?: Record<string, string> }) => {
      apiCalls.get.push(url + (config?.params ? '?' + new URLSearchParams(config.params).toString() : ''))
      if (url === '/jobs/') return { data: [JOB, { ...JOB, id: 'job-2', title: 'Office Boy' }], meta: { next: null, previous: null } }
      if (url === '/applications/') {
        const job = config?.params?.job_opening
        const rows = job ? APPLICATIONS.filter((a) => a.job_opening === job) : APPLICATIONS
        return { data: rows, meta: { next: null, previous: null } }
      }
      if (url === '/candidates/') return { data: [], meta: { next: null, previous: null } }
      if (url === '/applications/app-1/notifications/') return notifications.rows
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      apiCalls.post.push([url, body])
      if (url.endsWith('/retry-notification/')) {
        return { ...notifications.rows[0], status: 'sent', status_display: 'Sent', attempts: 2, error: '' }
      }
      return {}
    }),
  }
})

import { ApplyPage, anonymousClient } from '@/features/recruitment/ApplyPage'
import { CandidateEmailHistory } from '@/features/recruitment/CandidateEmailHistory'
import { CandidatesPage } from '@/features/recruitment/CandidatesPage'
import { ToastProvider } from '@/components/ui/Toast'
import { renderWithProviders } from './helpers'

function renderAt(path: string, element: JSX.Element, routePath: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <Routes>
            <Route path={routePath} element={element} />
          </Routes>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state.role = 'hr_head'
  apiCalls.get.length = 0
  apiCalls.post.length = 0
  notifications.rows = []
})

/* ------------------------------------------------------------ public form */

const POSTING = {
  title: 'Therapist', department: 'Medical', location: 'Pune', employment_type: 'full_time',
  description: 'Help people move.', requirements: 'BPT', accepts_applications: true,
  consent_text: 'I confirm the information provided is accurate.',
  fields: [
    { key: 'full_name', label: 'Full Name', type: 'text', target: 'candidate', required: true, options: [], help_text: '' },
    { key: 'email', label: 'Email', type: 'email', target: 'candidate', required: false, options: [], help_text: '' },
    { key: 'phone', label: 'Mobile Number', type: 'phone', target: 'candidate', required: true, options: [], help_text: '' },
    { key: 'experience_level', label: 'Experience Level', type: 'select', target: 'profile', required: false, options: ['Fresher', '1-3 years'], help_text: '' },
  ],
}

describe('the public application form', () => {
  it('loads the posting anonymously, blocks until required + consent, then submits once with a stable submission id', async () => {
    const requests: Array<{ method?: string; url?: string; data?: string; headers?: unknown }> = []
    const adapter = vi.fn(async (config: { method?: string; url?: string; data?: string; headers?: unknown }) => {
      requests.push(config)
      if (config.method === 'get') return { data: POSTING, status: 200, statusText: 'OK', headers: {}, config }
      return { data: { reference: 'APP-ABCD1234', job_title: 'Therapist' }, status: 201, statusText: 'Created', headers: {}, config }
    })
    anonymousClient.defaults.adapter = adapter as never

    const user = userEvent.setup()
    renderAt('/apply/tok123', <ApplyPage />, '/apply/:token')

    expect(await screen.findByRole('heading', { name: 'Therapist' })).toBeInTheDocument()
    expect(requests[0]?.url).toBe('/public/apply/tok123/')
    // Anonymous: no bearer token rides along.
    expect(JSON.stringify(requests[0]?.headers ?? {})).not.toMatch(/Bearer/)

    const submit = screen.getByRole('button', { name: /submit application/i })
    await user.click(submit)
    expect(await screen.findByText(/fill in the required answers/i)).toBeInTheDocument()
    expect(requests.filter((r) => r.method === 'post')).toHaveLength(0)

    await user.type(screen.getByLabelText(/full name/i), 'Nisha Verma')
    await user.type(screen.getByLabelText(/mobile number/i), '9876543210')
    await user.selectOptions(screen.getByLabelText(/experience level/i), '1-3 years')
    await user.click(submit)
    expect(await screen.findByText(/confirm the declaration/i)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox'))
    await user.click(submit)

    expect(await screen.findByTestId('reference')).toHaveTextContent('APP-ABCD1234')
    const posts = requests.filter((r) => r.method === 'post')
    expect(posts).toHaveLength(1)
    const body = JSON.parse(posts[0]!.data!)
    expect(body.consent).toBe(true)
    expect(body.answers).toMatchObject({ full_name: 'Nisha Verma', phone: '9876543210', experience_level: '1-3 years' })
    expect(typeof body.submission_id).toBe('string')
    expect(body.submission_id.length).toBeGreaterThan(8)
  })

  it('tells a candidate plainly when the link is dead or the job is closed', async () => {
    anonymousClient.defaults.adapter = (async (config: { method?: string }) => {
      if (config.method === 'get') {
        const err = new axios.AxiosError('nope', 'ERR_BAD_REQUEST', config as never, null, {
          data: {}, status: 404, statusText: 'Not Found', headers: {}, config: config as never,
        })
        throw err
      }
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }) as never
    renderAt('/apply/bad', <ApplyPage />, '/apply/:token')
    expect(await screen.findByText(/not valid/i)).toBeInTheDocument()

    anonymousClient.defaults.adapter = (async (config: { method?: string }) => ({
      data: { ...POSTING, accepts_applications: false }, status: 200, statusText: 'OK', headers: {}, config,
    })) as never
    renderAt('/apply/closed', <ApplyPage />, '/apply/:token')
    expect(await screen.findByText(/not accepting applications/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /submit application/i })).not.toBeInTheDocument()
  })
})

/* ------------------------------------------------------ email history */

describe('the candidate email history', () => {
  const failed = {
    id: 'n-1', application: 'app-1', candidate: 'c-1', job_opening: 'job-1',
    kind: 'hr_verification_passed', kind_display: 'HR verification passed',
    recipient_email: 'nisha@example.test', subject: 'Your application has progressed – Therapist',
    body_text: 'Dear Nisha…', status: 'failed', status_display: 'Failed', attempts: 1,
    last_attempt_at: '2026-08-12T10:00:00Z', sent_at: null, error: 'OSError: SMTP down',
    triggered_by_email: 'hr_manager@example.test', created_at: '2026-08-12T10:00:00Z',
  }
  const sent = { ...failed, id: 'n-0', kind: 'application_received', kind_display: 'Application received',
    status: 'sent', status_display: 'Sent', error: '', sent_at: '2026-08-10T09:00:00Z' }

  it('lists what was sent, and lets an editor retry only what failed', async () => {
    notifications.rows = [failed, sent]
    const user = userEvent.setup()
    renderWithProviders(<CandidateEmailHistory applicationId="app-1" mayRetry />)

    const table = await screen.findByTestId('email-history')
    expect(within(table).getByText('HR verification passed')).toBeInTheDocument()
    expect(within(table).getByText('Application received')).toBeInTheDocument()
    expect(within(table).getByText('Failed')).toBeInTheDocument()
    expect(within(table).getByText('Sent')).toBeInTheDocument()

    // One retry button — for the failed row, not the sent one.
    const retries = within(table).getAllByRole('button', { name: 'Retry' })
    expect(retries).toHaveLength(1)

    await user.click(retries[0]!)
    await waitFor(() =>
      expect(apiCalls.post).toContainEqual(['/applications/app-1/retry-notification/', { notification: 'n-1' }]),
    )
  })

  it('shows no retry to a viewer without EDIT', async () => {
    notifications.rows = [failed]
    renderWithProviders(<CandidateEmailHistory applicationId="app-1" mayRetry={false} />)
    const table = await screen.findByTestId('email-history')
    expect(within(table).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('reveals the failure and the body on demand', async () => {
    notifications.rows = [failed]
    const user = userEvent.setup()
    renderWithProviders(<CandidateEmailHistory applicationId="app-1" mayRetry />)
    await user.click(await screen.findByRole('button', { name: 'HR verification passed' }))
    expect(screen.getByText(/SMTP down/)).toBeInTheDocument()
    expect(screen.getByText(/Dear Nisha/)).toBeInTheDocument()
  })
})

/* ------------------------------------------------- job-specific candidates */

describe('the Candidates page, per job', () => {
  it('shows only that job’s applications when opened from a job, each with its job opening', async () => {
    renderAt('/recruitment/candidates?job_opening=job-1', <CandidatesPage />, '/recruitment/candidates')

    expect(await screen.findByText('Nisha Verma')).toBeInTheDocument()
    expect(screen.queryByText('Ravi Kumar')).not.toBeInTheDocument()
    // The applications endpoint was asked for THIS job, and only this job.
    expect(apiCalls.get.some((u) => u.startsWith('/applications/?') && u.includes('job_opening=job-1'))).toBe(true)
    // The job column is present.
    expect(screen.getAllByText('Therapist').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: /Candidates · Therapist/ })).toBeInTheDocument()
  })

  it('offers the application filters in the applications view', async () => {
    renderAt('/recruitment/candidates?view=applications', <CandidatesPage />, '/recruitment/candidates')
    expect(await screen.findByText('Nisha Verma')).toBeInTheDocument()
    expect(screen.getByText('Ravi Kumar')).toBeInTheDocument()
    expect(screen.getByLabelText('Job opening')).toBeInTheDocument()
    expect(screen.getByLabelText('Application status')).toBeInTheDocument()
    expect(screen.getByLabelText('Recruitment stage')).toBeInTheDocument()
    expect(screen.getByLabelText('Applied')).toBeInTheDocument()
    expect(screen.getByLabelText('Experience')).toBeInTheDocument()
  })
})

describe('one candidate record, two routes', () => {
  const CANDIDATE = {
    id: 'c-1', first_name: 'Nisha', last_name: 'Verma', full_name: 'Nisha Verma',
    email: 'nisha@example.test', phone: '9876543210', current_employer: 'Acme',
    total_experience_years: '2.0', expected_ctc: null, notice_period_days: null,
    source: 'google_forms',
    resume: true, has_resume: true, resume_name: 'Nisha-Verma-Resume.pdf',
    profile: { city: 'Pune', resume_link: 'https://drive.google.com/file/d/ABC/view', profile_link: 'linkedin.com/in/nisha', skills: 'python' },
    applications: [{ id: 'app-1', job_opening: 'job-1', job_title: 'Therapist', department_name: 'Medical', status: 'active', stage_name: 'HR verification', applied_at: '2026-08-10T09:00:00Z' }],
    consent_given: true, consent_at: '2026-08-10T09:00:00Z', final_decision_at: null, retention_until: null, created_at: '2026-08-10T09:00:00Z',
  }

  it('shows exactly the nine application-form fields, links clickable, resume downloadable', async () => {
    const { CandidateFormDetails } = await import('@/features/recruitment/CandidateFormDetails')
    renderWithProviders(<CandidateFormDetails candidate={CANDIDATE as never} />)
    // The nine questions, and only them.
    for (const label of ['Full Name', 'Email', 'Mobile Number', 'City', 'Education',
                         'Resume Upload', 'Resume Link', 'Current Salary', 'Expected Salary']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    for (const gone of ['Current employer', 'Experience', 'Notice period', 'Source', 'Consent']) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument()
    }
    expect(screen.getByText('Pune')).toBeInTheDocument()
    const drive = screen.getByRole('link', { name: 'https://drive.google.com/file/d/ABC/view' })
    expect(drive).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: 'nisha@example.test' })).toHaveAttribute('href', 'mailto:nisha@example.test')
    // The uploaded résumé is an authorised download, never a raw URL.
    const download = screen.getByRole('button', { name: /Download/ })
    expect(download).toHaveTextContent('Nisha-Verma-Resume.pdf')
    expect(document.querySelector('a[href="true"]')).toBeNull()
  })
})
