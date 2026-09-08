/**
 * Payroll settings: rates are entered by HR/Finance, certified by Finance
 * alone; salary structures are revised with full history.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const posts = vi.hoisted(() => [] as Array<{ url: string; body?: unknown }>)

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const pendingPf = {
  id: 'rs-1', statute: 'pf', jurisdiction: '', regime: '', financial_year: '',
  effective_from: '2026-10-01', effective_to: null, rule_version: 'pf.v1',
  parameters: {
    employee_rate: '12', employer_rate: '12', eps_rate: '8.33',
    wage_ceiling: '15000', eps_wage_ceiling: '15000',
  },
  source_citation: 'EPFO circular', source_url: '', retrieved_on: '2026-09-01',
  assumptions: [], verification_status: 'pending' as const,
  verified_by_email: null, verified_at: null, verification_note: '',
  rejection_reason: '', checksum: 'x', is_usable_for_payroll: false,
  is_tampered: false, was_self_verified: false,
}

const component = {
  id: 'comp-1', code: 'BASIC', name: 'Basic', component_type: 'earning',
  calc_type: 'fixed' as const, percent_of_code: '', is_taxable: true,
  is_part_of_ctc: true, is_wage: true, rounding: 'none', display_order: 1,
}

const structure = {
  id: 'st-1', employee: 'e-1', employee_name: 'Adnan Qureshi', employee_code: 'EMP01033',
  ctc_annual: '600000.00', valid_from: '2026-01-01', valid_to: null,
  revision_reason: 'Initial', monthly_gross: '50000.00', monthly_wage: '30000.00',
  wage_share: 0.6,
  pf_applicable: true, esi_applicable: true, pt_applicable: true,
  tds_applicable: true, gratuity_applicable: false,
  lines: [{
    component: 'comp-1', component_code: 'BASIC', component_name: 'Basic',
    is_wage: true, value: '30000.00', monthly_amount: '30000.00',
  }],
}

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/payroll/rule-sets/') return [pendingPf]
      if (url === '/payroll/components/') return [component]
      if (url === '/payroll/structures/') return { data: [structure], meta: { next: null, previous: null } }
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async (url: string, body?: unknown) => {
      posts.push({ url, body })
      if (url.includes('/verify/')) return { ...pendingPf, verification_status: 'verified' }
      return { id: 'new' }
    }),
  }
})

import { PayrollSettingsPage } from '@/features/payroll/PayrollSettingsPage'
import { CompensationSection } from '@/features/payroll/CompensationSection'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  state.role = 'hr_head'
  posts.length = 0
})

describe('Payroll settings', () => {
  it('lets HR Head draft PF rates — every figure typed, nothing pre-baked', async () => {
    const user = userEvent.setup()
    renderWithProviders(<PayrollSettingsPage />)

    const pfCard = (await screen.findByText('Provident Fund (PF & EPS)')).closest('section')!
    await user.click(within(pfCard as HTMLElement).getByRole('button', { name: /new rates/i }))

    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/effective from/i), '2026-10-01')
    await user.type(dialog.getByLabelText(/employee pf %/i), '12')
    await user.type(dialog.getByLabelText(/employer pf %/i), '12')
    await user.type(dialog.getByLabelText(/employer eps %/i), '8.33')
    await user.type(dialog.getByLabelText(/pf wage limit/i), '15000')
    await user.type(dialog.getByLabelText(/eps wage limit/i), '15000')
    await user.click(dialog.getByRole('button', { name: /create draft/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({
      url: '/payroll/rule-sets/',
      body: {
        statute: 'pf',
        rule_version: 'pf.v1',
        effective_from: '2026-10-01',
        parameters: {
          employee_rate: '12', employer_rate: '12', eps_rate: '8.33',
          wage_ceiling: '15000', eps_wage_ceiling: '15000',
        },
      },
    })
  })

  it('offers Verify only to the Finance Head', async () => {
    renderWithProviders(<PayrollSettingsPage />)
    await screen.findByText('Provident Fund (PF & EPS)')
    // HR Head sees the pending set but cannot certify it.
    expect(screen.queryByRole('button', { name: /^verify$/i })).not.toBeInTheDocument()
  })

  it('lets the Finance Head verify a pending rate set', async () => {
    state.role = 'finance_head'
    const user = userEvent.setup()
    renderWithProviders(<PayrollSettingsPage />)

    await user.click(await screen.findByRole('button', { name: /^verify$/i }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/what was checked/i), 'Matched circular.')
    await user.click(dialog.getByRole('button', { name: /^verify$/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].url).toBe('/payroll/rule-sets/rs-1/verify/')
  })
})

describe('Salary structure', () => {
  it('shows the structure in force and posts a revision with reason and history intact', async () => {
    const user = userEvent.setup()
    renderWithProviders(<CompensationSection employeeId="e-1" />)

    expect(await screen.findByText('In force')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /revise salary/i }))

    const dialog = within(await screen.findByRole('dialog'))
    const ctc = dialog.getByLabelText(/annual ctc/i)
    await user.clear(ctc)
    await user.type(ctc, '720000')
    await user.type(dialog.getByLabelText(/effective from/i), '2026-11-01')
    await user.type(dialog.getByLabelText(/reason/i), 'Annual raise')
    await user.click(dialog.getByRole('button', { name: /apply revision/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({
      url: '/payroll/structures/',
      body: {
        employee: 'e-1',
        ctc_annual: '720000',
        valid_from: '2026-11-01',
        revision_reason: 'Annual raise',
        lines: [{ component: 'comp-1', value: '30000.00' }],
      },
    })
  })

  it('gives employees no revise control', async () => {
    state.role = 'employee'
    renderWithProviders(<CompensationSection employeeId="e-1" />)
    expect(await screen.findByText('In force')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /revise salary/i })).not.toBeInTheDocument()
  })
})
