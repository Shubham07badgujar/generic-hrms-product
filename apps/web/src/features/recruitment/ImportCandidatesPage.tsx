/**
 * Bulk candidate import.
 *
 * A linear flow: platform → job → file → preview → attestation → result.
 *
 * State accumulates client-side and is committed in three calls (upload,
 * attest, commit). Nothing is saved per step — `EmployeesPage` makes the same
 * point about wizards: a flow that saves as it goes models a state the backend
 * refuses to have.
 *
 * THE BACKEND IS THE SOURCE OF TRUTH, TWICE OVER
 * ----------------------------------------------
 * The preview is a prediction, not a promise. Between preview and commit the
 * server re-authorises the user, re-resolves the job against their scope, and
 * re-resolves every row against the database — so this screen renders the
 * commit response as the outcome and never adds up the preview to guess it.
 *
 * PII
 * ---
 * Candidate details appear in the preview table, because deciding whether to
 * import them is the entire point. They are never put in the URL, in
 * localStorage, or in a toast, and the failed-row report deliberately carries
 * codes rather than cell values.
 */

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { Checkbox, FieldRow, Select, TextArea, TextInput } from '@/components/ui/Field'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { ImportRowBadge } from '@/components/ui/Badge'
import { Stepper } from '@/components/ui/Stepper'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import {
  fetchImportBatch,
  useAddImportRow,
  useAttestImport,
  useCommitImport,
  useCommitResultDownload,
  useImportPlatforms,
  useRemoveImportRow,
  useUpdateImportRow,
  useUploadImport,
} from '@/lib/importQueries'
import { Modal } from '@/components/ui/Modal'
import { useJobs } from '@/lib/queries'
import { IMPORT_CANONICAL_FIELDS } from '@/lib/types'
import type { ImportBatch, ImportCommitResult, ImportRow } from '@/lib/types'

const STEPS = [
  { id: 'platform', label: 'Platform' },
  { id: 'job', label: 'Job opening' },
  { id: 'file', label: 'Upload' },
  { id: 'preview', label: 'Preview' },
  { id: 'attest', label: 'Legal basis' },
  { id: 'result', label: 'Result' },
]

const LEGAL_BASES = [
  {
    value: 'voluntarily_provided',
    label: 'Voluntarily provided (DPDP s.7(a))',
    help:
      'These candidates posted a profile on the platform in order to be ' +
      'contacted about work, and have not objected to being contacted.',
  },
  {
    value: 'employer_subscription',
    label: 'Sourced through our platform subscription',
    help: 'Obtained through our own paid employer account on this platform.',
  },
]

const ATTESTATION_TEXT =
  'I confirm that a lawful basis exists for processing these candidates’ ' +
  'personal data, that the data was obtained from the named platform through ' +
  'our own employer account, and that a privacy notice will be provided ' +
  'before first contact.'

const MIN_NOTE = 20

/** Rows that will change something, versus rows that will not. */
const ACTIONABLE = new Set(['valid', 'created', 'matched_updated'])

export function ImportCandidatesPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const permissions = usePermissions()

  const [step, setStep] = useState(0)
  const [platform, setPlatform] = useState('')
  const [job, setJob] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [result, setResult] = useState<ImportCommitResult | null>(null)
  const [basis, setBasis] = useState(LEGAL_BASES[0].value)
  const [note, setNote] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  // Populated only when the server could not recognise the file's columns.
  // It hands the headers back with the refusal precisely so an otherwise
  // valid export is not dead-ended over column names we happen not to know.
  const [headers, setHeaders] = useState<string[]>([])
  const [mapping, setMapping] = useState<Record<string, string>>({})

  const platforms = useImportPlatforms()
  const jobs = useJobs({ status: 'published' })
  const upload = useUploadImport()
  const attest = useAttestImport()
  const commit = useCommitImport()
  const download = useCommitResultDownload()

  const mayImport = permissions.can(RESOURCE.CANDIDATE, ACTION.IMPORT)

  const selectedPlatform = useMemo(
    () => (platforms.data ?? []).find((item) => item.key === platform),
    [platforms.data, platform],
  )
  const available = (platforms.data ?? []).filter((item) => item.available)
  const unavailable = (platforms.data ?? []).filter((item) => !item.available)

  if (!mayImport) {
    // The route guard already refuses this; belt and braces for a direct render.
    return (
      <EmptyState
        title="You do not have permission to import candidates"
        description="Bulk import is granted separately from adding a candidate by hand."
      />
    )
  }

  function reset() {
    setStep(0)
    setPlatform('')
    setJob('')
    setFile(null)
    setBatch(null)
    setResult(null)
    setNote('')
    setConfirmed(false)
    setErrors({})
    setHeaders([])
    setMapping({})
  }

  function handleUpload() {
    if (!platform || !job || !file) return
    setErrors({})
    upload.mutate(
      {
        platform,
        job_opening: job,
        file,
        ...(Object.keys(mapping).length ? { column_override: mapping } : {}),
      },
      {
        onSuccess: (created) => {
          setBatch(created)
          setStep(3)
        },
        onError: (error) => {
          if (error instanceof ApiError) {
            setErrors(error.fieldErrors)
            // `detected_headers` means "readable file, unfamiliar columns" —
            // recoverable by mapping rather than a dead end.
            const detected = error.details?.detected_headers
            if (Array.isArray(detected) && detected.length) {
              setHeaders(detected as string[])
              setStep(6)
              return
            }
          }
          toast.fromError(error, 'That file could not be read')
        },
      },
    )
  }

  function handleCommit() {
    if (!batch) return
    setErrors({})
    attest.mutate(
      {
        batchId: batch.id,
        legal_basis: basis,
        legal_basis_note: note,
        attestation_text: ATTESTATION_TEXT,
      },
      {
        onSuccess: () => {
          commit.mutate(batch.id, {
            onSuccess: (committed) => {
              setResult(committed)
              setStep(5)
              toast.success(
                'Import finished',
                `${committed.created} created, ${committed.updated} updated.`,
              )
            },
            onError: (error) => {
              if (error instanceof ApiError) setErrors(error.fieldErrors)
              toast.fromError(error, 'The import could not be completed')
            },
          })
        },
        onError: (error) => {
          if (error instanceof ApiError) setErrors(error.fieldErrors)
          toast.fromError(error, 'The attestation was not accepted')
        },
      },
    )
  }

  const rows = batch?.rows ?? []
  const willChange = rows.filter((row) => ACTIONABLE.has(row.status)).length
  const noteTooShort = note.trim().length < MIN_NOTE

  // Two ways a mapping is unusable, both caught before the round trip:
  // nothing that yields a name, or the same field claimed twice — which the
  // parser would resolve by silently keeping whichever column came first.
  const chosen = Object.values(mapping)
  const duplicated = chosen.filter((value, index) => chosen.indexOf(value) !== index)
  const hasName = chosen.includes('full_name') || chosen.includes('first_name')
  const mappingProblem = !chosen.length
    ? 'Map at least one column.'
    : !hasName
      ? 'Map a column to Full name or First name — a candidate without a name cannot be imported.'
      : duplicated.length
        ? `Two columns are both mapped to ${duplicated[0].replace(/_/g, ' ')}. Each field can come from only one column.`
        : ''

  const busy = upload.isPending || attest.isPending || commit.isPending

  return (
    <>
      <PageHeader
        title="Import candidates"
        description="Load an applicant export from a job platform into a job opening's pipeline."
        actions={
          <Button variant="secondary" onClick={() => navigate('/recruitment/candidates')}>
            Back to candidates
          </Button>
        }
      />

      <Stepper steps={STEPS} current={step} className="mb-6" />

      {/* ---------------------------------------------------------- 1 platform */}
      {step === 0 && (
        <Section
          title="Where did this export come from?"
          description="Only platforms we can lawfully import from are selectable."
        >
          {platforms.isLoading && <LoadingBlock label="Loading platforms" />}
          {platforms.error && <ErrorState error={platforms.error} onRetry={platforms.refetch} />}

          {platforms.data && (
            <div className="space-y-3">
              {available.map((item) => (
                <Card key={item.key}>
                  <label className="flex cursor-pointer items-start gap-3">
                    <input
                      type="radio"
                      name="platform"
                      value={item.key}
                      checked={platform === item.key}
                      onChange={() => setPlatform(item.key)}
                      className="mt-1"
                    />
                    <span>
                      <span className="block font-medium text-ink">{item.label}</span>
                      <span className="block text-sm text-ink-subtle">{item.notes}</span>
                      <span className="mt-1 block text-xs text-ink-subtle">
                        Accepts {item.accepts.join(' and ')}
                      </span>
                    </span>
                  </label>
                </Card>
              ))}

              {unavailable.length > 0 && (
                <div className="space-y-3 pt-2">
                  <h3 className="text-sm font-semibold text-ink">Not available</h3>
                  {unavailable.map((item) => (
                    <Banner key={item.key} tone="neutral" title={item.label}>
                      {item.unavailable_reason}
                    </Banner>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="mt-6">
            <Button variant="primary" disabled={!platform} onClick={() => setStep(1)}>
              Continue
            </Button>
          </div>
        </Section>
      )}

      {/* --------------------------------------------------------------- 2 job */}
      {step === 1 && (
        <Section
          title="Which job are these candidates applying to?"
          description="Every imported candidate enters this job's hiring workflow at its first stage."
        >
          <Select
            label="Published job opening"
            required
            placeholder="Select a job"
            value={job}
            onChange={(event) => setJob(event.target.value)}
            options={(jobs.data?.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.title} — ${item.department_name}`,
            }))}
            error={errors.job_opening}
            description="You can only import into jobs you are allowed to work on."
          />
          <div className="mt-6 flex gap-2">
            <Button onClick={() => setStep(0)}>Back</Button>
            <Button variant="primary" disabled={!job} onClick={() => setStep(2)}>
              Continue
            </Button>
          </div>
        </Section>
      )}

      {/* -------------------------------------------------------------- 3 file */}
      {step === 2 && (
        <Section
          title="Upload the export"
          description={`Export the applicant list from ${selectedPlatform?.label ?? 'the platform'} and upload it here.`}
        >
          <div className="space-y-1.5">
            <label htmlFor="import-file" className="text-sm font-medium text-ink">
              File <span className="text-danger">*</span>
            </label>
            <input
              id="import-file"
              type="file"
              accept=".xlsx,.csv"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="w-full rounded-lg border border-line bg-surface p-2 text-sm"
            />
            <p className="text-xs text-ink-subtle">
              .xlsx or .csv, up to 5 MB and 2,000 rows. Macro-enabled workbooks are refused.
            </p>
            {errors.file && (
              <p role="alert" className="text-sm text-danger">
                {errors.file}
              </p>
            )}
          </div>

          {errors.non_field_errors && (
            <Banner tone="danger" title="The server refused this file" className="mt-4">
              {errors.non_field_errors}
            </Banner>
          )}

          <div className="mt-6 flex gap-2">
            <Button onClick={() => setStep(1)} disabled={busy}>
              Back
            </Button>
            <Button
              variant="primary"
              loading={upload.isPending}
              disabled={!file || busy}
              onClick={handleUpload}
            >
              Upload and preview
            </Button>
          </div>
        </Section>
      )}

      {/* ----------------------------------------------------------- 4 preview */}
      {step === 3 && batch && (
        <Section
          title="Preview"
          description="Nothing has been imported yet. Check what will happen to each row."
        >
          <PreviewSummary batch={batch} />

          {batch.rows_failed > 0 && (
            <Banner tone="warning" title="Some rows cannot be imported" className="mb-4">
              {batch.rows_failed} row{batch.rows_failed === 1 ? '' : 's'} will be skipped.
              The rest will still import; you can fix those rows and import the file again
              without creating duplicates.
            </Banner>
          )}

          <StagingTable batch={batch} onBatchChange={setBatch} />

          <div className="mt-6 flex gap-2">
            <Button onClick={reset} disabled={busy}>
              Start over
            </Button>
            <Button
              variant="primary"
              disabled={willChange === 0 || busy}
              onClick={() => setStep(4)}
            >
              Continue
            </Button>
          </div>
          {willChange === 0 && (
            <p className="mt-2 text-sm text-ink-subtle">
              Nothing in this file would change anything, so there is nothing to import.
            </p>
          )}
        </Section>
      )}

      {/* ---------------------------------------------------------- 5 attest */}
      {step === 4 && batch && (
        <Section
          title="Legal basis"
          description="Required before any candidate data is stored."
        >
          <Banner tone="info" title="This is not consent">
            These people agreed to the platform's terms, not to ours. Recording the ground
            you are relying on — and providing a privacy notice before first contact — is
            what makes holding their data lawful.
          </Banner>

          <div className="mt-4 space-y-4">
            <Select
              label="Ground for processing"
              required
              value={basis}
              onChange={(event) => setBasis(event.target.value)}
              options={LEGAL_BASES.map((item) => ({ value: item.value, label: item.label }))}
              description={LEGAL_BASES.find((item) => item.value === basis)?.help}
              error={errors.legal_basis}
            />

            <TextArea
              label="How were these candidates obtained?"
              required
              rows={3}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              error={errors.legal_basis_note}
              description={`At least ${MIN_NOTE} characters — ${note.trim().length}/${MIN_NOTE}. This is recorded against every candidate in the file.`}
            />

            <Card>
              <p className="text-sm text-ink">{ATTESTATION_TEXT}</p>
              <div className="mt-3">
                <Checkbox
                  label="I confirm the above"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
              </div>
            </Card>
          </div>

          {errors.detail && (
            <Banner tone="danger" title="The server refused this" className="mt-4">
              {errors.detail}
            </Banner>
          )}

          <div className="mt-6 flex gap-2">
            <Button onClick={() => setStep(3)} disabled={busy}>
              Back
            </Button>
            <Button
              variant="primary"
              loading={attest.isPending || commit.isPending}
              disabled={!confirmed || noteTooShort || busy}
              onClick={handleCommit}
            >
              Import {willChange} candidate{willChange === 1 ? '' : 's'}
            </Button>
          </div>
        </Section>
      )}

      {/* --------------------------------------------------------- 7 mapping */}
      {step === 6 && (
        <Section
          title="Map the columns"
          description="We could not recognise this file's column names. Tell us what they mean and we will read it again."
        >
          <Banner tone="info" title="Nothing has been imported">
            The file was read safely — its columns simply are not ones we know.
            Mapping them here changes only how the columns are interpreted; every
            size, row and content limit still applies.
          </Banner>

          <div className="mt-4 space-y-3">
            {headers.map((header) => (
              <FieldRow key={header}>
                <TextInput label="Column in your file" value={header} readOnly disabled />
                <Select
                  label="Means"
                  placeholder="Ignore this column"
                  value={mapping[header] ?? ''}
                  onChange={(event) => {
                    const value = event.target.value
                    setMapping((current) => {
                      const next = { ...current }
                      if (value) next[header] = value
                      else delete next[header]
                      return next
                    })
                  }}
                  options={IMPORT_CANONICAL_FIELDS.map((item) => ({
                    value: item.value,
                    label: item.required ? `${item.label} (required)` : item.label,
                  }))}
                />
              </FieldRow>
            ))}
          </div>

          {mappingProblem && (
            <Banner tone="warning" title="This mapping will not work" className="mt-4">
              {mappingProblem}
            </Banner>
          )}
          {errors.column_override && (
            <Banner tone="danger" title="The server refused this mapping" className="mt-4">
              {errors.column_override}
            </Banner>
          )}

          <div className="mt-6 flex gap-2">
            <Button onClick={reset} disabled={busy}>
              Start over
            </Button>
            <Button
              variant="primary"
              loading={upload.isPending}
              disabled={Boolean(mappingProblem) || busy}
              onClick={handleUpload}
            >
              Read the file again
            </Button>
          </div>
        </Section>
      )}

      {/* ---------------------------------------------------------- 6 result */}
      {step === 5 && result && (
        <Section
          title="Import finished"
          description="These numbers come from the server, not from the preview."
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard label="Created" value={result.created} tone="success" />
            <StatCard label="Updated" value={result.updated} tone="brand" />
            <StatCard label="Duplicate" value={result.duplicate} tone="neutral" />
            <StatCard label="Needs review" value={result.review} tone="warning" />
            <StatCard label="Failed" value={result.failed} tone={result.failed ? 'danger' : 'neutral'} />
          </div>

          {(result.failed > 0 || result.review > 0) && (
            <Card className="mt-6">
              <CardHeader
                title="Rows that need attention"
                description="Fix these in your export and import it again — the rows that already landed will not be duplicated."
                action={
                  <Button
                    onClick={() => download(result.batch.id, result.batch.original_filename)}
                  >
                    Download report
                  </Button>
                }
              />
              <ul className="mt-3 space-y-2 text-sm">
                {result.failures.map((failure) => (
                  <li key={failure.row} className="flex gap-3">
                    <span className="font-mono text-ink-subtle">Row {failure.row}</span>
                    <span className="text-ink">{humanCode(failure.code)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="mt-6 flex gap-2">
            <Button variant="primary" onClick={() => navigate('/recruitment/pipeline')}>
              View the pipeline
            </Button>
            <Button onClick={reset}>Import another file</Button>
          </div>
        </Section>
      )}
    </>
  )
}

/* ------------------------------------------------------------------ parts */

function PreviewSummary({ batch }: { batch: ImportBatch }) {
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="Rows read" value={batch.rows_total} />
      <StatCard label="From" value={batch.platform_label} hint={batch.original_filename} />
      <StatCard label="Into" value={batch.job_title} />
      <StatCard
        label="Cannot import"
        value={batch.rows_failed}
        tone={batch.rows_failed ? 'warning' : 'neutral'}
      />
    </div>
  )
}

function StagingTable({
  batch,
  onBatchChange,
}: {
  batch: ImportBatch
  onBatchChange: (batch: ImportBatch) => void
}) {
  const toast = useToast()
  const updateRow = useUpdateImportRow()
  const addRow = useAddImportRow()
  const removeRow = useRemoveImportRow()
  const [editing, setEditing] = useState<ImportRow | 'new' | null>(null)

  const headers = batch.detected_headers ?? []
  const rows = batch.rows ?? []

  async function refresh() {
    onBatchChange(await fetchImportBatch(batch.id))
  }

  if (!rows.length && !headers.length) {
    return <EmptyState title="This file has no rows" compact />
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          Every column of the file, editable before anything is imported. Fix a cell, add a
          walk-in candidate, or drop a row — validity and duplicate checks re-run on save.
        </p>
        <Button size="sm" variant="secondary" onClick={() => setEditing('new')}>
          + Add row
        </Button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line bg-canvas text-left text-xs uppercase tracking-wide text-ink-subtle">
              <th className="sticky left-0 z-10 bg-canvas px-3 py-2">Row</th>
              <th className="px-3 py-2">Outcome</th>
              {headers.map((header) => (
                <th key={header} className="max-w-[220px] truncate px-3 py-2" title={header}>
                  {header}
                </th>
              ))}
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row) => (
              <tr key={row.id} className="align-top">
                <td className="sticky left-0 z-10 bg-surface px-3 py-2 tabular-nums text-ink-subtle">
                  {row.row_number}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">
                  <ImportRowBadge status={row.status} />
                  {[...row.errors, ...row.warnings].length > 0 && (
                    <p className="mt-1 max-w-[180px] text-xs text-ink-subtle">
                      {[...row.errors, ...row.warnings]
                        .map((issue) => humanCode(issue.code))
                        .join('; ')}
                    </p>
                  )}
                </td>
                {headers.map((header) => (
                  <td
                    key={header}
                    className="max-w-[220px] truncate px-3 py-2 text-ink"
                    title={row.raw?.[header] ?? ''}
                  >
                    {row.raw?.[header] || <span className="text-ink-subtle">—</span>}
                  </td>
                ))}
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(row)}>
                    Edit
                  </Button>{' '}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      removeRow.mutate(
                        { batchId: batch.id, row_number: row.row_number },
                        {
                          onSuccess: () => void refresh(),
                          onError: (error) => toast.fromError(error, 'Could not remove'),
                        },
                      )
                    }
                  >
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={headers.length + 3} className="px-3 py-6 text-center text-ink-subtle">
                  No rows staged — add one to begin.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {editing !== null && (
        <RowEditorDialog
          headers={headers}
          row={editing === 'new' ? null : editing}
          busy={updateRow.isPending || addRow.isPending}
          onClose={() => setEditing(null)}
          onSave={(raw) => {
            const done = {
              onSuccess: () => {
                toast.success(editing === 'new' ? 'Row added' : 'Row updated')
                setEditing(null)
                void refresh()
              },
              onError: (error: unknown) => toast.fromError(error, 'Could not save the row'),
            }
            if (editing === 'new') {
              addRow.mutate({ batchId: batch.id, raw }, done)
            } else {
              updateRow.mutate(
                { batchId: batch.id, row_number: (editing as ImportRow).row_number, raw },
                done,
              )
            }
          }}
        />
      )}
    </div>
  )
}

function RowEditorDialog({
  headers,
  row,
  busy,
  onClose,
  onSave,
}: {
  headers: string[]
  row: ImportRow | null
  busy: boolean
  onClose: () => void
  onSave: (raw: Record<string, string>) => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    for (const header of headers) initial[header] = row?.raw?.[header] ?? ''
    return initial
  })

  return (
    <Modal
      open
      onClose={onClose}
      busy={busy}
      size="lg"
      title={row ? `Edit row ${row.row_number}` : 'Add a candidate row'}
      description="Saved cells go through the same parsing and duplicate checks as the uploaded file."
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} onClick={() => onSave(values)}>
            {row ? 'Save row' : 'Add row'}
          </Button>
        </>
      }
    >
      <div className="grid max-h-[60vh] gap-3 overflow-y-auto pr-1 sm:grid-cols-2">
        {headers.map((header) => (
          <TextInput
            key={header}
            label={header}
            value={values[header] ?? ''}
            onChange={(event) =>
              setValues((current) => ({ ...current, [header]: event.target.value }))
            }
          />
        ))}
      </div>
    </Modal>
  )
}

/**
 * Turn a backend code into something an HR user can act on.
 *
 * The backend sends codes rather than sentences precisely so that the same
 * object can be logged and audited without carrying candidate data; the wording
 * belongs here.
 */
function humanCode(code: string): string {
  const messages: Record<string, string> = {
    no_identity_key: 'No email, phone or platform ID — nothing to recognise them by',
    missing_name: 'No name in this row',
    ambiguous_phone_match: 'That phone number belongs to more than one candidate',
    phone_match_name_mismatch: 'Phone matches an existing candidate with a different name',
    email_belongs_to_other_candidate: 'That email is already used by a different candidate',
    phone_belongs_to_other_candidate: 'That phone is already used by a different candidate',
    matched_inactive_candidate: 'Matches a candidate who was previously removed',
    application_soft_deleted: 'A removed application blocks re-applying — restore it first',
    empty_or_uncached_formula: 'This cell contains a formula with no saved value',
    row_failed: 'This row could not be imported',
  }
  return messages[code] ?? code.replace(/_/g, ' ')
}
