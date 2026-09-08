/**
 * A Recruiter must be able to fill in the whole job opening form.
 *
 * The form's four required fields are backed by reference lists the Recruiter
 * previously could not read: department, hiring workflow and role on hire all
 * returned 403, so their dropdowns rendered empty, the form's readiness check
 * never passed, and the save button stayed disabled. The permission was held
 * and could not be exercised.
 *
 * These tests drive the real `JobsPage` as a Recruiter and assert the form
 * reaches a submittable state with the right payload — the frontend half of
 * the matrix change, so the two cannot drift apart silently.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'recruiter' as string }))
const saveJob = vi.hoisted(() => vi.fn())

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

/*
 * The reference lists are mocked at the query layer rather than over HTTP.
 * What is under test is whether the form can be completed from the data a
 * Recruiter is allowed to load — the authorisation itself is proven server-side
 * in tests/cutover/test_recruiter_job_opening.py, which calls the real
 * endpoints as a real Recruiter.
 */
const MEDICAL = { id: 'dept-med', name: 'Medical', kind: 'medical' }
const OPERATIONS = { id: 'dept-ops', name: 'Operations', kind: 'operations' }

const MEDICAL_WORKFLOW = {
  id: 'wf-med',
  name: 'Therapist pipeline',
  department_kind: 'medical',
  is_published: true,
  stage_count: 6,
}
const OPS_WORKFLOW = {
  id: 'wf-ops',
  name: 'Office Boy pipeline',
  department_kind: 'operations',
  is_published: true,
  stage_count: 3,
}
const DRAFT_WORKFLOW = {
  id: 'wf-draft',
  name: 'Unfinished pipeline',
  department_kind: 'medical',
  is_published: false,
  stage_count: 2,
}

vi.mock('@/lib/queries', () => ({
  useDepartments: () => ({ data: [MEDICAL, OPERATIONS], isLoading: false }),
  useWorkflows: () => ({
    data: { data: [MEDICAL_WORKFLOW, OPS_WORKFLOW, DRAFT_WORKFLOW] },
    isLoading: false,
  }),
  useRoles: () => ({
    data: [
      { id: 'role-therapist', code: 'therapist', name: 'Therapist', is_grantable: true, requires_employee: true },
      { id: 'role-ceo', code: 'ceo', name: 'CEO', is_grantable: true, requires_employee: false },
      { id: 'role-admin', code: 'admin', name: 'Admin', is_grantable: false, requires_employee: false },
    ],
    isLoading: false,
  }),
  useDesignations: () => ({ data: [{ id: 'desig-1', title: 'Clinic Doctor' }], isLoading: false }),
  useLocations: () => ({ data: [{ id: 'loc-1', name: 'Head Office' }], isLoading: false }),
  useEmployeeLevels: () => ({ data: [{ id: 'lvl-1', name: 'Staff' }], isLoading: false }),
  useSaveJob: () => ({ mutate: saveJob, isPending: false }),
  useJobs: () => ({
    // `meta` carries the cursors — the list renders a pager unconditionally.
    data: { data: [], meta: { next: null, previous: null } },
    isLoading: false,
    isError: false,
  }),
  useJob: () => ({ data: undefined, isLoading: false }),
  useWorkflow: () => ({ data: undefined, isLoading: false }),
  useJobLifecycle: () => ({ publish: { mutate: vi.fn() }, close: { mutate: vi.fn() } }),
  useApplications: () => ({ data: { data: [] }, isLoading: false }),
}))

import { JobsPage } from '@/features/recruitment/JobsPage'
import { renderWithProviders } from './helpers'

function optionsOf(select: HTMLElement) {
  return within(select)
    .queryAllByRole('option')
    .map((o) => o.textContent?.trim())
    .filter((text) => text && !/^select|^optional/i.test(text))
}

/*
 * The page offers two ways in — the header action and the empty-state prompt —
 * so these are matched as a set. Which one is clicked does not matter; that
 * both exist for a Recruiter and neither exists without the grant does.
 */
function createButtons() {
  return screen.queryAllByRole('button', { name: /^new job$/i })
}

/*
 * Everything is queried inside the dialog. The list behind it has its own
 * "Job title" column header, so an unscoped label lookup matches two things
 * and the test fails for a reason that has nothing to do with the form.
 */
async function openTheForm() {
  const user = userEvent.setup()
  renderWithProviders(<JobsPage />)
  await user.click(createButtons()[0])
  const form = within(await screen.findByRole('dialog'))
  expect(form.getByText(/new job opening/i)).toBeInTheDocument()
  return { user, form }
}

beforeEach(() => {
  state.role = 'recruiter'
  saveJob.mockReset()
})

describe('the job opening form, as a Recruiter', () => {
  it('offers the New job button', async () => {
    renderWithProviders(<JobsPage />)
    expect(createButtons().length).toBeGreaterThan(0)
    expect(createButtons()[0]).toBeEnabled()
  })

  it('populates every required dropdown', async () => {
    const { form } = await openTheForm()

    expect(optionsOf(form.getByLabelText(/department/i)).length).toBeGreaterThan(0)
    expect(optionsOf(form.getByLabelText(/role on hire/i)).length).toBeGreaterThan(0)
    // The workflow list is department-dependent, so it is asserted below once a
    // department has been chosen.
  })

  it('populates the optional dropdowns too', async () => {
    const { form } = await openTheForm()

    expect(optionsOf(form.getByLabelText(/designation/i))).toContain('Clinic Doctor')
    expect(optionsOf(form.getByLabelText(/location/i))).toContain('Head Office')
    expect(optionsOf(form.getByLabelText(/seniority level/i))).toContain('Staff')
  })

  it('filters workflows by the chosen department', async () => {
    const { user, form } = await openTheForm()

    await user.selectOptions(form.getByLabelText(/department/i), 'dept-med')

    const offered = optionsOf(form.getByLabelText(/hiring workflow/i)).join(' ')
    expect(offered).toContain('Therapist pipeline')
    expect(offered).not.toContain('Office Boy pipeline')
  })

  it('never offers a draft workflow', async () => {
    const { user, form } = await openTheForm()

    await user.selectOptions(form.getByLabelText(/department/i), 'dept-med')

    // Same function as the medical workflow, so only its draft status keeps it
    // out. The server refuses it too — this stops it being offered at all.
    const offered = optionsOf(form.getByLabelText(/hiring workflow/i)).join(' ')
    expect(offered).not.toContain('Unfinished pipeline')
  })

  it('offers only roles a candidate can actually be hired into', async () => {
    const { form } = await openTheForm()

    const offered = optionsOf(form.getByLabelText(/role on hire/i)).join(' ')
    expect(offered).toContain('Therapist')
    // Both are excluded by the same rule the backend uses: a role that needs no
    // employee record is not something a candidate is hired into.
    expect(offered).not.toContain('CEO')
    expect(offered).not.toContain('Admin')
  })

  it('submits the completed form with the values that were picked', async () => {
    const { user, form } = await openTheForm()

    await user.type(form.getByLabelText(/job title/i), 'Senior Physiotherapist')
    await user.selectOptions(form.getByLabelText(/department/i), 'dept-med')
    await user.selectOptions(form.getByLabelText(/hiring workflow/i), 'wf-med')
    await user.selectOptions(form.getByLabelText(/role on hire/i), 'role-therapist')
    await user.selectOptions(form.getByLabelText(/designation/i), 'desig-1')
    await user.type(form.getByLabelText(/salary/i), '₹4–6 LPA')
    await user.type(form.getByLabelText(/age limit/i), '35')
    await user.selectOptions(form.getByLabelText(/gender preference/i), 'female')

    const submit = form.getByRole('button', { name: /create draft/i })
    await waitFor(() => expect(submit).toBeEnabled())
    await user.click(submit)

    expect(saveJob).toHaveBeenCalledTimes(1)
    expect(saveJob.mock.calls[0][0]).toMatchObject({
      title: 'Senior Physiotherapist',
      department: 'dept-med',
      workflow: 'wf-med',
      target_role: 'role-therapist',
      designation: 'desig-1',
      salary: '₹4–6 LPA',
      age_limit: 35,
      gender_preference: 'female',
    })
  })

  it('keeps submission disabled until every required field is set', async () => {
    const { user, form } = await openTheForm()

    const submit = form.getByRole('button', { name: /create draft/i })
    expect(submit).toBeDisabled()

    await user.type(form.getByLabelText(/job title/i), 'Half filled')
    await user.selectOptions(form.getByLabelText(/department/i), 'dept-med')
    expect(submit).toBeDisabled()

    await user.selectOptions(form.getByLabelText(/hiring workflow/i), 'wf-med')
    expect(submit).toBeDisabled()

    await user.selectOptions(form.getByLabelText(/role on hire/i), 'role-therapist')
    expect(submit).toBeDisabled()

    // The office's posting criteria are required too: a designation and salary.
    await user.selectOptions(form.getByLabelText(/designation/i), 'desig-1')
    expect(submit).toBeDisabled()

    await user.type(form.getByLabelText(/salary/i), '₹4–6 LPA')
    await waitFor(() => expect(submit).toBeEnabled())
  })
})

describe('roles without the grant', () => {
  it('does not offer job creation to an ordinary employee', async () => {
    state.role = 'employee'
    renderWithProviders(<JobsPage />)

    expect(createButtons()).toHaveLength(0)
  })
})
