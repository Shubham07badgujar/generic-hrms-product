/**
 * The candidate import wizard.
 *
 * Three things this suite is really about:
 *
 *   1. Only the four roles holding CANDIDATE/IMPORT can reach it.
 *   2. The flow cannot be short-circuited — no preview without a platform, job
 *      and file; no import without an attestation.
 *   3. The RESULT comes from the commit response, never from the preview. The
 *      preview is a prediction the server is free to contradict.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'recruiter' as string }))
const uploadFn = vi.hoisted(() => vi.fn())
const attestFn = vi.hoisted(() => vi.fn())
const commitFn = vi.hoisted(() => vi.fn())
const downloadFn = vi.hoisted(() => vi.fn())

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const PLATFORMS = [
  {
    key: 'workindia',
    label: 'WorkIndia',
    available: true,
    unavailable_reason: '',
    notes: 'Export from the WorkIndia employer dashboard.',
    accepts: ['.csv', '.xlsx'],
  },
  {
    key: 'naukri',
    label: 'Naukri',
    available: true,
    unavailable_reason: '',
    notes: 'Export from Naukri Response Management.',
    accepts: ['.csv', '.xlsx'],
  },
  {
    key: 'linkedin',
    label: 'LinkedIn',
    available: false,
    unavailable_reason:
      'LinkedIn is not currently accepting new partnerships for its Job Posting API, ' +
      'and offers no native bulk applicant export.',
    notes: '',
    accepts: [],
  },
  {
    key: 'indeed',
    label: 'Indeed',
    available: false,
    unavailable_reason:
      'Indeed requires a signed Developer Agreement and formal partner approval.',
    notes: '',
    accepts: [],
  },
  {
    key: 'internshala',
    label: 'Internshala',
    available: false,
    unavailable_reason: 'Internshala publishes no employer API.',
    notes: '',
    accepts: [],
  },
]

const BATCH = {
  id: 'batch-1',
  platform: 'workindia',
  platform_label: 'WorkIndia',
  job_opening: 'job-1',
  job_title: 'Therapist',
  original_filename: 'workindia.xlsx',
  file_sha256: 'abc123',
  column_mapping: {},
  status: 'parsed',
  rows_total: 4,
  rows_created: 0,
  rows_updated: 0,
  rows_duplicate: 0,
  rows_review: 0,
  rows_failed: 1,
  legal_basis: '',
  attested_at: null,
  committed_at: null,
  created_at: '2026-08-17T00:00:00Z',
  rows: [
    {
      id: 'r1', row_number: 2, first_name: 'Priya', last_name: 'Deshmukh',
      email: 'priya@example.test', phone: '9876543210', external_id: 'WI-1',
      current_employer: '', total_experience_years: null, expected_ctc: null,
      notice_period_days: null, status: 'valid', match_rule: 'none',
      matched_candidate: null, duplicate_of_row: null, errors: [], warnings: [],
    },
    {
      id: 'r2', row_number: 3, first_name: 'Ramesh', last_name: 'Kumar',
      email: null, phone: '9876543211', external_id: 'WI-2',
      current_employer: '', total_experience_years: null, expected_ctc: null,
      notice_period_days: null, status: 'matched_skipped', match_rule: 'phone',
      matched_candidate: 'cand-9', duplicate_of_row: null, errors: [], warnings: [],
    },
    {
      id: 'r3', row_number: 4, first_name: 'Vikram', last_name: 'M',
      email: null, phone: '9876543210', external_id: '',
      current_employer: '', total_experience_years: null, expected_ctc: null,
      notice_period_days: null, status: 'needs_review', match_rule: 'phone',
      matched_candidate: null, duplicate_of_row: null,
      errors: [], warnings: [{ row: 4, code: 'ambiguous_phone_match' }],
    },
    {
      id: 'r4', row_number: 5, first_name: '', last_name: '',
      email: null, phone: '', external_id: '',
      current_employer: '', total_experience_years: null, expected_ctc: null,
      notice_period_days: null, status: 'invalid', match_rule: '',
      matched_candidate: null, duplicate_of_row: null,
      errors: [{ row: 5, code: 'missing_name' }], warnings: [],
    },
  ],
}

vi.mock('@/lib/importQueries', () => ({
  useImportPlatforms: () => ({ data: PLATFORMS, isLoading: false, error: null, refetch: vi.fn() }),
  useUploadImport: () => ({ mutate: uploadFn, isPending: false }),
  useAttestImport: () => ({ mutate: attestFn, isPending: false }),
  useCommitImport: () => ({ mutate: commitFn, isPending: false }),
  useCommitResultDownload: () => downloadFn,
  useImportErrors: () => ({ data: undefined, isLoading: false }),
  useUpdateImportRow: () => ({ mutate: vi.fn(), isPending: false }),
  useAddImportRow: () => ({ mutate: vi.fn(), isPending: false }),
  useRemoveImportRow: () => ({ mutate: vi.fn(), isPending: false }),
  fetchImportBatch: vi.fn(),
}))

vi.mock('@/lib/queries', () => ({
  useJobs: () => ({
    data: {
      data: [{ id: 'job-1', title: 'Therapist', department_name: 'Medical' }],
      meta: { next: null, previous: null },
    },
    isLoading: false,
    isError: false,
  }),
}))

import { ImportCandidatesPage } from '@/features/recruitment/ImportCandidatesPage'
import { fillIn, renderWithProviders } from './helpers'

function file(name = 'workindia.xlsx') {
  return new File(['col\nvalue'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
}

/** Drive the wizard as far as the preview, with the upload succeeding. */
async function toPreview(user: ReturnType<typeof userEvent.setup>) {
  uploadFn.mockImplementation((_input, opts) => opts.onSuccess(BATCH))

  await user.click(screen.getByRole('radio', { name: /workindia/i }))
  await user.click(screen.getByRole('button', { name: /continue/i }))
  await user.selectOptions(screen.getByLabelText(/published job opening/i), 'job-1')
  await user.click(screen.getByRole('button', { name: /continue/i }))
  await user.upload(screen.getByLabelText(/^file/i), file())
  await user.click(screen.getByRole('button', { name: /upload and preview/i }))
  await screen.findByText(/nothing has been imported yet/i)
}

beforeEach(() => {
  state.role = 'recruiter'
  uploadFn.mockReset()
  attestFn.mockReset()
  commitFn.mockReset()
  downloadFn.mockReset()
})

/* --------------------------------------------------------------- RBAC */

describe('who can import', () => {
  it.each(['recruiter', 'hr_manager', 'hr_head', 'admin'])(
    'lets %s start an import',
    async (role) => {
      state.role = role
      renderWithProviders(<ImportCandidatesPage />)

      expect(screen.getByRole('heading', { name: /import candidates/i })).toBeInTheDocument()
      expect(screen.getByText(/where did this export come from/i)).toBeInTheDocument()
    },
  )

  it.each(['employee', 'clinic_doctor', 'medical_director', 'ceo'])(
    'refuses %s',
    async (role) => {
      state.role = role
      renderWithProviders(<ImportCandidatesPage />)

      expect(screen.getByText(/do not have permission to import/i)).toBeInTheDocument()
      expect(screen.queryByText(/where did this export come from/i)).toBeNull()
    },
  )

  it('does not offer the CEO an import, since read-only means read-only', () => {
    state.role = 'ceo'
    renderWithProviders(<ImportCandidatesPage />)

    expect(screen.queryByRole('radio', { name: /workindia/i })).toBeNull()
  })
})

/* ---------------------------------------------------------- platforms */

describe('platform selection', () => {
  it('offers the platforms we can lawfully import from', () => {
    renderWithProviders(<ImportCandidatesPage />)

    expect(screen.getByRole('radio', { name: /workindia/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /naukri/i })).toBeInTheDocument()
  })

  it('shows why the other three cannot be imported from', () => {
    renderWithProviders(<ImportCandidatesPage />)

    // Stated, not hidden — omitting them invites the assumption that support
    // is merely pending.
    expect(screen.getByText(/not currently accepting new partnerships/i)).toBeInTheDocument()
    expect(screen.getByText(/signed developer agreement/i)).toBeInTheDocument()
    expect(screen.getByText(/publishes no employer api/i)).toBeInTheDocument()
  })

  it('gives an unavailable platform no way to be selected', () => {
    renderWithProviders(<ImportCandidatesPage />)

    expect(screen.queryByRole('radio', { name: /linkedin/i })).toBeNull()
  })

  it('cannot continue without choosing a platform', () => {
    renderWithProviders(<ImportCandidatesPage />)

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })
})

/* -------------------------------------------------------- step gating */

describe('the flow cannot be short-circuited', () => {
  it('requires a job before the upload step', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)

    await user.click(screen.getByRole('radio', { name: /workindia/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })

  it('requires a file before it will upload', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)

    await user.click(screen.getByRole('radio', { name: /workindia/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.selectOptions(screen.getByLabelText(/published job opening/i), 'job-1')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(screen.getByRole('button', { name: /upload and preview/i })).toBeDisabled()
  })

  it('reaches the preview only after a successful upload', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toPreview(user)

    expect(uploadFn).toHaveBeenCalledTimes(1)
    expect(uploadFn.mock.calls[0][0]).toMatchObject({
      platform: 'workindia',
      job_opening: 'job-1',
    })
  })
})

/* ------------------------------------------------------------ preview */

describe('the preview', () => {
  it('shows what will happen to every row', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toPreview(user)

    expect(screen.getByText('Will create')).toBeInTheDocument()
    expect(screen.getByText('Already on file')).toBeInTheDocument()
    expect(screen.getByText('Needs review')).toBeInTheDocument()
    expect(screen.getByText('Invalid')).toBeInTheDocument()
  })

  it('explains a row that cannot be imported in words, not codes', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toPreview(user)

    expect(screen.getByText(/belongs to more than one candidate/i)).toBeInTheDocument()
    expect(screen.getByText(/no name in this row/i)).toBeInTheDocument()
  })

  it('says plainly that nothing has been imported yet', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toPreview(user)

    expect(screen.getByText(/nothing has been imported yet/i)).toBeInTheDocument()
  })
})

/* -------------------------------------------------------- attestation */

describe('the attestation', () => {
  async function toAttest(user: ReturnType<typeof userEvent.setup>) {
    await toPreview(user)
    await user.click(screen.getByRole('button', { name: /^continue$/i }))
    await screen.findByText(/this is not consent/i)
  }

  it('will not import until the box is ticked', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toAttest(user)

    await fillIn(
      user,
      screen.getByLabelText(/how were these candidates obtained/i),
      'Exported from our own WorkIndia employer account.',
    )

    expect(screen.getByRole('button', { name: /^import/i })).toBeDisabled()
  })

  it('will not import on a note too short to mean anything', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toAttest(user)

    await user.type(screen.getByLabelText(/how were these candidates obtained/i), 'because')
    await user.click(screen.getByRole('checkbox', { name: /i confirm the above/i }))

    expect(screen.getByRole('button', { name: /^import/i })).toBeDisabled()
  })

  it('states that this is a legal basis and not consent', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toAttest(user)

    expect(screen.getByText(/this is not consent/i)).toBeInTheDocument()
    expect(screen.getByText(/agreed to the platform's terms, not to ours/i)).toBeInTheDocument()
  })

  it('sends the attestation before committing', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toAttest(user)

    attestFn.mockImplementation((_input, opts) => opts.onSuccess(BATCH))
    commitFn.mockImplementation((_id, opts) =>
      opts.onSuccess({
        batch: { ...BATCH, status: 'partial' },
        created: 1, updated: 0, duplicate: 1, review: 1, failed: 1,
        failures: [{ row: 5, code: 'missing_name' }],
      }),
    )

    await fillIn(
      user,
      screen.getByLabelText(/how were these candidates obtained/i),
      'Exported from our own WorkIndia employer account on 17 August.',
    )
    await user.click(screen.getByRole('checkbox', { name: /i confirm the above/i }))
    await user.click(screen.getByRole('button', { name: /^import/i }))

    await waitFor(() => expect(commitFn).toHaveBeenCalled())
    expect(attestFn).toHaveBeenCalledTimes(1)
    expect(attestFn.mock.calls[0][0]).toMatchObject({
      batchId: 'batch-1',
      legal_basis: 'voluntarily_provided',
    })
    expect(attestFn.mock.calls[0][0].attestation_text).toContain('lawful basis')
  })
})

/* --------------------------------------------------------------- result */

describe('the result', () => {
  async function toResult(user: ReturnType<typeof userEvent.setup>) {
    await toPreview(user)
    await user.click(screen.getByRole('button', { name: /^continue$/i }))
    await screen.findByText(/this is not consent/i)

    attestFn.mockImplementation((_input, opts) => opts.onSuccess(BATCH))
    commitFn.mockImplementation((_id, opts) =>
      opts.onSuccess({
        batch: { ...BATCH, status: 'partial' },
        created: 1, updated: 0, duplicate: 1, review: 1, failed: 1,
        failures: [{ row: 5, code: 'missing_name' }],
      }),
    )

    await fillIn(
      user,
      screen.getByLabelText(/how were these candidates obtained/i),
      'Exported from our own WorkIndia employer account on 17 August.',
    )
    await user.click(screen.getByRole('checkbox', { name: /i confirm the above/i }))
    await user.click(screen.getByRole('button', { name: /^import/i }))
    // The toast repeats this wording, so match the heading, not the text.
    await screen.findByRole('heading', { name: /import finished/i })
  }

  it('reports the numbers the SERVER returned, not the preview', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toResult(user)

    // The preview showed one "will create" row; the commit response is what is
    // displayed, and it is free to disagree — another user may have created
    // that candidate in between.
    expect(screen.getByText(/these numbers come from the server/i)).toBeInTheDocument()
    expect(screen.getByText('Created')).toBeInTheDocument()
    expect(screen.getByText('Needs review')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('lists the rows that need attention', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toResult(user)

    expect(screen.getByText(/row 5/i)).toBeInTheDocument()
    expect(screen.getByText(/no name in this row/i)).toBeInTheDocument()
  })

  it('offers the failed rows as a download', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toResult(user)

    await user.click(screen.getByRole('button', { name: /download report/i }))

    expect(downloadFn).toHaveBeenCalledWith('batch-1', 'workindia.xlsx')
  })

  it('tells the user a re-import will not duplicate what already landed', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toResult(user)

    expect(screen.getByText(/will not be duplicated/i)).toBeInTheDocument()
  })
})


/* ------------------------------------------------------- column mapping */

describe('manual column mapping', () => {
  /** Drive to the mapping step by having the upload refuse with headers. */
  async function toMapping(user: ReturnType<typeof userEvent.setup>) {
    const { ApiError } = await import('@/lib/api')
    const refusal = new ApiError(400, {
      error: {
        code: 'invalid',
        message: 'Validation failed.',
        details: {
          file: ["None of this file's columns could be recognised."],
          detected_headers: ['Naam', 'Number', 'Kaam'],
        },
      },
    })
    uploadFn.mockImplementation((_input, opts) => opts.onError(refusal))

    await user.click(screen.getByRole('radio', { name: /workindia/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.selectOptions(screen.getByLabelText(/published job opening/i), 'job-1')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.upload(screen.getByLabelText(/^file/i), file())
    await user.click(screen.getByRole('button', { name: /upload and preview/i }))
    await screen.findByText(/map the columns/i)
  }

  it('offers mapping instead of dead-ending an unrecognised export', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    // The headers the file actually declared, handed back with the refusal.
    expect(screen.getByDisplayValue('Naam')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Number')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Kaam')).toBeInTheDocument()
  })

  it('offers only canonical HRMS fields as targets', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    const select = screen.getAllByLabelText(/^means$/i)[0]
    const options = within(select)
      .getAllByRole('option')
      .map((option) => option.getAttribute('value'))

    expect(options).toContain('full_name')
    expect(options).toContain('phone')
    // Never a model field that would let a mapping write consent or state.
    expect(options).not.toContain('consent_given')
    expect(options).not.toContain('is_active')
    expect(options).not.toContain('legal_basis')
  })

  it('marks the fields a row cannot do without', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    expect(screen.getAllByRole('option', { name: /full name \(required\)/i }).length).toBeGreaterThan(0)
  })

  it('refuses to re-read until something maps to a name', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    expect(screen.getByRole('button', { name: /read the file again/i })).toBeDisabled()
    expect(screen.getByText(/map at least one column/i)).toBeInTheDocument()

    await user.selectOptions(screen.getAllByLabelText(/^means$/i)[1], 'phone')

    // A phone is not a name, and a candidate without a name cannot be imported.
    expect(screen.getByText(/map a column to full name or first name/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /read the file again/i })).toBeDisabled()
  })

  it('refuses a mapping that claims one field twice', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    await user.selectOptions(screen.getAllByLabelText(/^means$/i)[0], 'full_name')
    await user.selectOptions(screen.getAllByLabelText(/^means$/i)[1], 'full_name')

    // The parser would resolve this by silently keeping the first column.
    expect(screen.getByText(/both mapped to full name/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /read the file again/i })).toBeDisabled()
  })

  it('re-uploads with the mapping once it is valid', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ImportCandidatesPage />)
    await toMapping(user)

    await user.selectOptions(screen.getAllByLabelText(/^means$/i)[0], 'full_name')
    await user.selectOptions(screen.getAllByLabelText(/^means$/i)[1], 'phone')

    uploadFn.mockImplementation((_input, opts) => opts.onSuccess(BATCH))
    await user.click(screen.getByRole('button', { name: /read the file again/i }))
    await screen.findByText(/nothing has been imported yet/i)

    const lastCall = uploadFn.mock.calls.at(-1)![0]
    expect(lastCall.column_override).toEqual({ Naam: 'full_name', Number: 'phone' })
    // "Kaam" was left unmapped, so it is simply not carried across.
    expect(lastCall.column_override).not.toHaveProperty('Kaam')
  })
})
