/**
 * Interview feedback at a round with no assessment form.
 *
 * Round 3 of a custom workflow had no form, and the drawer refused outright
 * with "No assessment form is configured". The recommendation, rating,
 * strengths and concerns ARE the feedback; the form only adds structured
 * questions. Without one, the drawer offers the general fields and submits
 * with empty answers.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_manager' as string }))
const posts = vi.hoisted(() => [] as Array<[string, unknown]>)

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/feedback-forms/') return { data: [], meta: { next: null, previous: null } }
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async (url: string, body: unknown) => {
      posts.push([url, body])
      return { id: 'fb-1', recommendation: 'hire' }
    }),
  }
})

import { FeedbackDrawer } from '@/features/recruitment/FeedbackForm'
import { renderWithProviders } from './helpers'
import type { Interview } from '@/lib/types'

const INTERVIEW = {
  id: 'iv-1', application: 'app-1', candidate_name: 'Shubham Pramod Badgujar', stage: 's-50',
  stage_name: 'Round 3', feedback_form: null, interviewer: 'e-hrm', interviewer_name: 'Hari Menon',
  scheduled_at: '2026-08-20T09:00:00Z', scheduled_end: '2026-08-20T09:45:00Z', duration_minutes: 45,
  mode: 'video', location_or_link: '', status: 'scheduled', feedback_submitted: false,
} as unknown as Interview

beforeEach(() => {
  posts.length = 0
})

describe('feedback without an assessment form', () => {
  it('offers the recommendation and notes, and submits them with no structured answers', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <FeedbackDrawer open onClose={() => {}} interview={INTERVIEW} formId={null} />,
    )
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.queryByText(/No assessment form is configured/)).not.toBeInTheDocument()
    expect(dialog.getByText(/No structured assessment form is attached/)).toBeInTheDocument()

    const recommendation = dialog.getByLabelText(/overall recommendation/i)
    await user.selectOptions(recommendation, 'hire')
    await user.click(dialog.getByLabelText(/strengths/i))
    await user.paste('Clear communicator')

    await user.click(dialog.getByRole('button', { name: /submit feedback/i }))
    await waitFor(() => expect(posts.length).toBe(1))
    const [url, body] = posts[0]!
    expect(url).toBe('/interviews/iv-1/feedback/')
    expect(body).toMatchObject({ answers: {}, recommendation: 'hire', strengths: 'Clear communicator' })
  })
})
