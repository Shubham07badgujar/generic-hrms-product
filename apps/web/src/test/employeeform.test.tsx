/**
 * The Add Employee form's two dropdowns, against the production data shape.
 *
 * Both were reported empty or incomplete for the HR Head. The causes:
 *
 *   ROLE — every assignable role WAS returned, but choosing a department
 *   greyed out every role from another function, and sixteen options with
 *   twelve disabled reads as "roles are missing". The list is now filtered
 *   to what fits, and says how many that is.
 *
 *   MANAGER — the query asked for `status=active`. In this system a confirmed
 *   employee has moved PAST "active", and in a mature organisation nearly
 *   everyone is confirmed, so the list was empty for exactly the people who
 *   belong on it. The fixture below has NOBODY with status "active", which is
 *   what production looks like.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
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

const role = (code: string, layer: number, kind = '', extra: Record<string, unknown> = {}) => ({
  id: `role-${code}`,
  code,
  name: code.replace(/_/g, ' '),
  layer,
  department_kind: kind,
  is_grantable: true,
  requires_employee: true,
  is_read_only: false,
  can_manage_users: false,
  is_system: true,
  dashboard_key: 'self',
  ...extra,
})

const ROLES = [
  role('admin', 1, '', { is_grantable: false, requires_employee: false }),
  role('ceo', 1, '', { requires_employee: false, is_read_only: true }),
  role('hr_head', 2, 'hr'),
  role('medical_director', 2, 'medical'),
  role('hr_manager', 3, 'hr'),
  role('senior_doctor', 3, 'medical'),
  role('recruiter', 4, 'hr'),
  role('clinic_doctor', 4, 'medical'),
  role('therapist', 5, 'medical'),
  role('employee', 5, ''),
]

const DEPARTMENTS = [
  { id: 'dep-med', name: 'Medical', code: 'MED', kind: 'medical', head_employee_name: null },
  { id: 'dep-hr', name: 'HR', code: 'HR', kind: 'hr', head_employee_name: null },
]

// Nobody here is status "active" — everyone is confirmed, as in production.
const EMPLOYEES = [
  { id: 'e-md', employee_code: 'EMP01017', full_name: 'Meera Kulkarni', status: 'confirmed', department: 'dep-med', department_name: 'Medical', designation_title: 'Medical Director', roles: ['medical_director'] },
  { id: 'e-sd', employee_code: 'EMP01021', full_name: 'Sanjay Iyer', status: 'confirmed', department: 'dep-med', department_name: 'Medical', designation_title: 'Senior Doctor', roles: ['senior_doctor'] },
  { id: 'e-cd', employee_code: 'EMP01025', full_name: 'Chandni Bose', status: 'confirmed', department: 'dep-med', department_name: 'Medical', designation_title: 'Clinic Doctor', roles: ['clinic_doctor'] },
  { id: 'e-hh', employee_code: 'EMP01019', full_name: 'Hema Rao', status: 'confirmed', department: 'dep-hr', department_name: 'HR', designation_title: 'HR Head', roles: ['hr_head'] },
  { id: 'e-hm', employee_code: 'EMP01023', full_name: 'Hari Menon', status: 'confirmed', department: 'dep-hr', department_name: 'HR', designation_title: 'HR Manager', roles: ['hr_manager'] },
  { id: 'e-gone', employee_code: 'EMP01099', full_name: 'Gone Person', status: 'exited', department: 'dep-med', department_name: 'Medical', designation_title: '', roles: ['senior_doctor'] },
  { id: 'e-norole', employee_code: 'EMP01098', full_name: 'No Login', status: 'confirmed', department: 'dep-med', department_name: 'Medical', designation_title: '', roles: [] },
]

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string, config?: { params?: Record<string, string> }) => {
      if (url === '/roles/') return ROLES
      if (url === '/departments/') return DEPARTMENTS
      if (url === '/designations/')
        return [{ id: 'des-1', title: 'Therapist', department: null, department_name: null }]
      if (url === '/locations/' || url === '/levels/') return []
      if (url === '/employees/') {
        // Reproduce the server: `status=active` genuinely matches nobody.
        const wanted = config?.params?.status
        const rows = wanted ? EMPLOYEES.filter((e) => e.status === wanted) : EMPLOYEES
        return { data: rows, meta: { next: null, previous: null, page_size: 40, count: rows.length, page: 1, pages: 1 } }
      }
      return { data: [], meta: { next: null, previous: null, page_size: 40 } }
    }),
    apiPost: vi.fn(async () => ({})),
  }
})

import { EmployeesPage } from '@/features/employees/EmployeesPage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  state.role = 'hr_head'
})

async function openForm() {
  const user = userEvent.setup()
  renderWithProviders(<EmployeesPage />)
  await user.click(await screen.findByRole('button', { name: /add employee/i }))
  const dialog = within(await screen.findByRole('dialog'))
  await waitFor(() => expect(dialog.getByLabelText(/^role/i)).toBeInTheDocument())
  return { user, dialog }
}

function optionLabels(select: HTMLElement) {
  return Array.from((select as HTMLSelectElement).options)
    .map((option) => option.textContent ?? '')
    .filter((label) => label && !/select a role|optional/i.test(label))
}

describe('the Role dropdown', () => {
  it('offers every assignable role before a department is chosen', async () => {
    const { dialog } = await openForm()

    const labels = optionLabels(dialog.getByLabelText(/^role/i))
    // 8 of the 10 fixture roles: admin (not grantable) and ceo (no employee) excluded.
    expect(labels).toHaveLength(8)
    expect(labels.some((l) => /medical director/i.test(l))).toBe(true)
    expect(labels.some((l) => /therapist/i.test(l))).toBe(true)
    expect(labels.some((l) => /^admin/i.test(l))).toBe(false)
  })

  it('narrows to the department function plus agnostic roles, offering rather than greying', async () => {
    const { user, dialog } = await openForm()

    await user.selectOptions(dialog.getByLabelText(/department/i), 'dep-med')

    const select = dialog.getByLabelText(/^role/i) as HTMLSelectElement
    const labels = optionLabels(select)
    // medical_director, senior_doctor, clinic_doctor, therapist + agnostic 'employee'
    expect(labels).toHaveLength(5)
    expect(labels.some((l) => /hr manager/i.test(l))).toBe(false)
    expect(Array.from(select.options).every((o) => !o.disabled)).toBe(true)
  })
})

describe('the Reporting Manager dropdown', () => {
  it('is populated even though nobody has status "active"', async () => {
    const { dialog } = await openForm()

    const labels = optionLabels(dialog.getByLabelText(/reporting manager/i))
    expect(labels.length).toBeGreaterThan(0)
    expect(labels.some((l) => /meera kulkarni/i.test(l))).toBe(true)
  })

  it('never offers the exited or the role-less', async () => {
    const { dialog } = await openForm()

    const labels = optionLabels(dialog.getByLabelText(/reporting manager/i))
    expect(labels.some((l) => /gone person/i.test(l))).toBe(false)
    expect(labels.some((l) => /no login/i.test(l))).toBe(false)
  })

  it('offers the same level and above, from any department', async () => {
    const { user, dialog } = await openForm()

    await user.selectOptions(dialog.getByLabelText(/department/i), 'dep-med')
    await user.selectOptions(dialog.getByLabelText(/^role/i), 'clinic_doctor')

    const labels = optionLabels(dialog.getByLabelText(/reporting manager/i))
    // Senior to an L4 clinic doctor: MD (L2), Senior Doctor (L3), HR Head
    // (L2), and — cross-department, by policy — the HR Manager (L3) too.
    expect(labels.some((l) => /meera kulkarni/i.test(l))).toBe(true)
    expect(labels.some((l) => /sanjay iyer/i.test(l))).toBe(true)
    expect(labels.some((l) => /hema rao/i.test(l))).toBe(true)
    expect(labels.some((l) => /hari menon/i.test(l))).toBe(true)
    // A PEER is offered too: real structures are not a strict ladder, and a
    // peer manager grants no extra authority — the role decides permission.
    expect(labels.some((l) => /chandni bose/i.test(l))).toBe(true)
  })

  it('never offers anyone more junior', async () => {
    const { user, dialog } = await openForm()

    await user.selectOptions(dialog.getByLabelText(/department/i), 'dep-med')
    await user.selectOptions(dialog.getByLabelText(/^role/i), 'medical_director')

    const labels = optionLabels(dialog.getByLabelText(/reporting manager/i))
    // An L2 head cannot report to an L4 clinic doctor — that line would run
    // downward and invert every approval it routes.
    expect(labels.some((l) => /chandni bose/i.test(l))).toBe(false)
    // …but another L2 head is a valid choice.
    expect(labels.some((l) => /meera kulkarni/i.test(l))).toBe(true)
  })

  it('is hidden from the Admin and shown to the HR Head', async () => {
    const { dialog } = await openForm()
    expect(dialog.getByLabelText(/reporting manager/i)).toBeInTheDocument()
  })
})


describe('the Designation field', () => {
  it('is marked required and offers the catalogue', async () => {
    const { dialog } = await openForm()

    const select = dialog.getByLabelText(/designation/i) as HTMLSelectElement
    expect(select).toBeRequired()
    expect(
      Array.from(select.options).some((o) => /therapist/i.test(o.textContent ?? '')),
    ).toBe(true)
  })

  it('blocks submission until a designation is chosen', async () => {
    const { user, dialog } = await openForm()

    await user.click(dialog.getByLabelText(/^first name/i))
    await user.paste('Nisha')
    await user.click(dialog.getByLabelText(/company email/i))
    await user.paste('nisha@example.test')
    await user.click(dialog.getByLabelText(/personal email/i))
    await user.paste('nisha.personal@example.test')
    await user.selectOptions(dialog.getByLabelText(/department/i), 'dep-med')
    await user.selectOptions(dialog.getByLabelText(/^role/i), 'therapist')
    await user.click(dialog.getByLabelText(/date of joining/i))
    await user.paste('2026-09-01')

    // A non-head role also requires a reporting manager before submitting.
    const managerSelect = dialog.getByLabelText(/reporting manager/i) as HTMLSelectElement
    const managerOption = [...managerSelect.options].find((o) => o.value)
    await user.selectOptions(managerSelect, managerOption!.value)

    // Everything else is filled; the designation alone holds it back.
    const submit = dialog.getByRole('button', { name: /create employee|create/i })
    expect(submit).toBeDisabled()

    await user.selectOptions(dialog.getByLabelText(/designation/i), 'des-1')
    expect(submit).toBeEnabled()
  })
})
