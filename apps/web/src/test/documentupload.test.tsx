/**
 * Uploading a document from a profile, driven through the real component.
 *
 * The existing lifecycle tests render `DocumentsSection` but never open the
 * upload dialog, so the path from "click Upload document" to "a multipart
 * request leaves the browser" has never been exercised. Users report that
 * uploading from their profile does not work, and the API is demonstrably
 * healthy for every role — which puts the fault in this layer.
 *
 * The HTTP layer is stubbed at the module boundary so the request that WOULD
 * have gone out can be inspected: the assertion is about what the component
 * sends, not about what a server does with it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'employee' as string }))

/** Captures every request the component makes. */
const sent = vi.hoisted(() => ({
  posts: [] as Array<{ url: string; body: unknown; headers?: Record<string, unknown> }>,
  gets: [] as string[],
}))

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
      sent.gets.push(url)
      if (url === '/document-types/') {
        // The real production shape: a bare array, not an envelope.
        return [
          {
            id: 'type-pan',
            name: 'PAN card',
            code: 'pan-card',
            category: 'identity',
            description: '',
            is_mandatory: true,
            requires_expiry: false,
          },
        ]
      }
      return []
    }),
    apiPost: vi.fn(async () => ({})),
    http: {
      ...actual.http,
      post: vi.fn(async (url: string, body: unknown, config?: Record<string, unknown>) => {
        sent.posts.push({ url, body, headers: config?.headers as Record<string, unknown> })
        return { data: { id: 'doc-new' } }
      }),
    },
  }
})

import { DocumentsSection } from '@/features/employees/ProfileSections'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  sent.posts.length = 0
  sent.gets.length = 0
  state.role = 'employee'
})

describe('uploading a document from a profile', () => {
  it('offers the upload control to a role holding CREATE', () => {
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[]} />)

    expect(screen.getByRole('button', { name: /upload document/i })).toBeInTheDocument()
  })

  it('lists the document types the API returned', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[]} />)

    await user.click(screen.getByRole('button', { name: /upload document/i }))
    const dialog = within(screen.getByRole('dialog'))

    await waitFor(() => {
      expect(dialog.getByRole('option', { name: /PAN card/i })).toBeInTheDocument()
    })
  })

  it('sends a multipart request carrying the employee, type and file', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[]} />)

    await user.click(screen.getByRole('button', { name: /upload document/i }))
    const dialog = within(screen.getByRole('dialog'))

    await waitFor(() => dialog.getByRole('option', { name: /PAN card/i }))
    await user.selectOptions(dialog.getByLabelText(/document type/i), 'type-pan')

    const file = new File(['%PDF-1.4'], 'pan.pdf', { type: 'application/pdf' })
    await user.upload(dialog.getByLabelText(/^file/i), file)

    await user.click(dialog.getByRole('button', { name: /^upload$/i }))

    await waitFor(() => expect(sent.posts).toHaveLength(1))

    const request = sent.posts[0]!
    expect(request.url).toBe('/employee-documents/')

    const form = request.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('employee')).toBe('employee-1')
    expect(form.get('document_type')).toBe('type-pan')
    expect(form.get('file')).toBeInstanceOf(File)

    // The browser must be left to set the multipart boundary itself.
    expect(request.headers?.['Content-Type']).toBeUndefined()
  })

  it('keeps upload unavailable for a role without CREATE', () => {
    state.role = 'ceo'
    renderWithProviders(<DocumentsSection employeeId="employee-1" documents={[]} />)

    expect(screen.queryByRole('button', { name: /upload document/i })).not.toBeInTheDocument()
  })
})
