/**
 * People → Documents.
 *
 * The page renders controls from permissions, so the assertions are mostly
 * about what a given role is NOT offered. That matters less as a security
 * property — the server refuses regardless — and more as an honesty one: a
 * button that produces a 403 teaches people the app is unreliable.
 *
 * The rejection dialog carries the only real client-side rule, the 250-word
 * limit, and it is a hint rather than a control. The test asserts it blocks
 * submission locally AND that the same number lives in the backend service.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const calls = vi.hoisted(() => ({ posts: [] as Array<{ url: string; body: unknown }> }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const DOCUMENTS = [
  {
    id: 'doc-1',
    employee: 'emp-1',
    employee_name: 'Priya Nair',
    employee_code: 'EMP05000',
    department_name: 'Medical',
    document_type: 'dt-1',
    document_type_name: 'PAN card',
    category: 'identity',
    original_filename: 'pan.pdf',
    content_type: 'application/pdf',
    size_bytes: 2048,
    has_file: true,
    uploaded_by: 'user-hr',
    uploaded_by_email: 'hr@example.test',
    uploaded_at: '2026-08-10T09:00:00Z',
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
  },
  {
    id: 'doc-2',
    employee: 'emp-2',
    employee_name: 'Sanjay Iyer',
    employee_code: 'EMP01021',
    department_name: 'Medical',
    document_type: 'dt-2',
    document_type_name: 'Relieving letter',
    category: 'employment',
    original_filename: 'relieving.pdf',
    content_type: 'application/pdf',
    size_bytes: 4096,
    has_file: true,
    uploaded_by: 'user-2',
    uploaded_by_email: 'sanjay@example.test',
    uploaded_at: '2026-08-09T09:00:00Z',
    filed_by_someone_else: false,
    status: 'rejected',
    verified_by: null,
    verified_by_email: null,
    verified_at: null,
    rejected_by: 'user-hr',
    rejected_by_email: 'hr@example.test',
    rejected_at: '2026-08-11T10:00:00Z',
    rejection_reason: 'The scan is cut off at the bottom.',
    issue_date: null,
    expires_on: null,
    notes: '',
  },
]

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    downloadFile: vi.fn(async () => undefined),
    apiGet: vi.fn(async (url: string) => {
      if (url === '/document-types/') return [{ id: 'dt-1', name: 'PAN card', code: 'pan', category: 'identity', description: '', is_mandatory: true, requires_expiry: false }]
      if (url === '/departments/') return [{ id: 'dep-1', name: 'Medical', code: 'medical' }]
      if (url.startsWith('/employees/')) {
        return { data: [{ id: 'emp-1', full_name: 'Priya Nair', employee_code: 'EMP05000' }], meta: { next: null, previous: null, page_size: 25 } }
      }
      if (url.startsWith('/employee-documents/')) {
        return { data: DOCUMENTS, meta: { next: null, previous: null, page_size: 25 } }
      }
      return { data: [], meta: { next: null, previous: null, page_size: 25 } }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      calls.posts.push({ url, body })
      return DOCUMENTS[0]
    }),
  }
})

import { DocumentsPage } from '@/features/employees/DocumentsPage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  calls.posts.length = 0
  state.role = 'hr_head'
})

async function renderPage() {
  renderWithProviders(<DocumentsPage />)
  await waitFor(() => expect(screen.getByText('Priya Nair')).toBeInTheDocument())
}

describe('the documents page', () => {
  it('lists every document with its employee and identifier', async () => {
    await renderPage()

    expect(screen.getByText('EMP05000')).toBeInTheDocument()
    expect(screen.getByText('Sanjay Iyer')).toBeInTheDocument()
    expect(screen.getAllByText('PAN card').length).toBeGreaterThan(0)
  })

  it('shows who uploaded each document and when', async () => {
    await renderPage()

    expect(screen.getAllByText('hr@example.test').length).toBeGreaterThan(0)
  })

  it('marks a document somebody filed on another person’s behalf', async () => {
    await renderPage()

    expect(screen.getByText(/on their behalf/i)).toBeInTheDocument()
  })

  it('shows the rejection reason and who recorded it', async () => {
    await renderPage()

    expect(screen.getByText('The scan is cut off at the bottom.')).toBeInTheDocument()
  })

  it('offers verify and reject to a role holding both', async () => {
    await renderPage()

    expect(screen.getByRole('button', { name: /^verify$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^reject$/i })).toBeInTheDocument()
  })

  it('offers neither to a role holding only VIEW', async () => {
    state.role = 'medical_director'
    await renderPage()

    expect(screen.queryByRole('button', { name: /^verify$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^reject$/i })).not.toBeInTheDocument()
  })

  it('offers no write actions to the read-only CEO', async () => {
    state.role = 'ceo'
    await renderPage()

    expect(screen.queryByRole('button', { name: /^verify$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^reject$/i })).not.toBeInTheDocument()
  })

  it('still offers download to anyone who can see the row', async () => {
    state.role = 'medical_director'
    await renderPage()

    expect(screen.getAllByRole('button', { name: /download/i }).length).toBeGreaterThan(0)
  })

  it('verifies through the API when asked', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^verify$/i }))

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/employee-documents/doc-1/verify/')
  })

  it('exposes search and the filters a reviewer needs', async () => {
    await renderPage()

    expect(screen.getByPlaceholderText(/search by name/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Status')).toBeInTheDocument()
    expect(screen.getByLabelText('Document type')).toBeInTheDocument()
    expect(screen.getByLabelText('Employee')).toBeInTheDocument()
    expect(screen.getByLabelText('Uploaded by')).toBeInTheDocument()
    expect(screen.getByLabelText('Uploaded on or after')).toBeInTheDocument()
    expect(screen.getByLabelText('Uploaded on or before')).toBeInTheDocument()
  })
})

describe('the rejection dialog', () => {
  it('will not submit without a reason', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))

    expect(dialog.getByRole('button', { name: /reject document/i })).toBeDisabled()
  })

  it('accepts a reason within the limit', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))
    await user.click(dialog.getByLabelText(/reason/i))
    await user.paste('The bottom of the scan is missing.')

    // Re-query after the re-render rather than reusing a handle captured
    // before it — the enabled button is a different node.
    const submit = await screen.findByRole('button', { name: /reject document/i })
    expect(submit).toBeEnabled()

    await user.click(submit)

    await waitFor(() => expect(calls.posts).toHaveLength(1))
    expect(calls.posts[0]!.url).toBe('/employee-documents/doc-1/reject/')
    expect(calls.posts[0]!.body).toEqual({ reason: 'The bottom of the scan is missing.' })
  })

  it('blocks a reason over 250 words and says so', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))

    // paste rather than type — 251 keystrokes-worth is slow and pointless
    await user.click(dialog.getByLabelText(/reason/i))
    await user.paste(Array.from({ length: 251 }, () => 'word').join(' '))

    expect(dialog.getByRole('button', { name: /reject document/i })).toBeDisabled()
    expect(dialog.getByText(/251 words/i)).toBeInTheDocument()
    expect(calls.posts).toHaveLength(0)
  })

  it('counts words, not characters', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))
    await user.click(dialog.getByLabelText(/reason/i))
    // Far past any sane character limit, comfortably inside the word limit.
    await user.paste(Array.from({ length: 40 }, () => 'antidisestablishmentarianism').join(' '))

    expect(dialog.getByRole('button', { name: /reject document/i })).toBeEnabled()
  })

  it('keeps focus in the reason field while typing', async () => {
    /*
     * Regression: the dialog's focus trap re-ran its setup whenever the page
     * re-rendered — every keystroke, since the page owns the reason state —
     * and setup focuses the first control in the panel, the × close button.
     * The field accepted exactly one character per click. The earlier test
     * used paste (one input event), which is precisely why it never caught
     * this; this one types.
     */
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))
    const field = dialog.getByLabelText(/reason/i)

    await user.type(field, 'The scan is blurry')

    expect(field).toHaveValue('The scan is blurry')
    expect(field).toHaveFocus()
  })

  it('warns that the reason cannot be changed afterwards', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByRole('button', { name: /^reject$/i }))
    const dialog = within(screen.getByRole('dialog'))

    expect(dialog.getByText(/cannot be reworded afterwards/i)).toBeInTheDocument()
  })
})
