/** Candidate list, creation, and the full candidate profile. */

import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Card, CardHeader, PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, LoadingBlock, TableSkeleton } from '@/components/ui/States'
import { ApplicationStatusBadge, Badge, RecommendationBadge } from '@/components/ui/Badge'
import { Avatar, Banner, Timeline } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Checkbox, FieldRow, Select, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import {
  useCandidate,
  useCandidateHistory,
  useCreateApplication,
  useCreateCandidate,
  useJobs,
} from '@/lib/queries'
import { useApplications, useCandidates } from '@/lib/queries'
import { CandidateFormDetails } from './CandidateFormDetails'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { formatDate, formatDateTime, humanize } from '@/lib/format'
import type { Application, Candidate } from '@/lib/types'

/* ---------------------------------------------------------------- create */

function CandidateFormModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const create = useCreateCandidate()
  const [values, setValues] = useState({
    first_name: '',
    last_name: '',
    email: '',
    phone: '',
    current_employer: '',
    total_experience_years: '',
    expected_ctc: '',
    notice_period_days: '',
    source: 'direct',
  })
  const [consent, setConsent] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  function set(key: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [key]: value }))
    setErrors(({ [key]: _removed, ...rest }) => rest)
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={create.isPending}
      title="Add candidate"
      description="Recording a candidate creates a personal-data record, so consent is required."
      footer={
        <>
          <Button onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!values.first_name || !values.email || !consent}
            onClick={() => {
              setErrors({})
              create.mutate(
                {
                  ...values,
                  total_experience_years: values.total_experience_years || null,
                  expected_ctc: values.expected_ctc || null,
                  notice_period_days: values.notice_period_days
                    ? Number(values.notice_period_days)
                    : null,
                  consent_given: consent,
                } as Partial<Candidate>,
                {
                  onSuccess: () => {
                    toast.success('Candidate added')
                    onClose()
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not add the candidate')
                  },
                },
              )
            }}
          >
            Add candidate
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FieldRow>
          <TextInput
            label="First name"
            required
            value={values.first_name}
            onChange={(event) => set('first_name', event.target.value)}
            error={errors.first_name}
          />
          <TextInput
            label="Last name"
            value={values.last_name}
            onChange={(event) => set('last_name', event.target.value)}
            error={errors.last_name}
          />
        </FieldRow>
        <FieldRow>
          <TextInput
            label="Email"
            type="email"
            required
            value={values.email}
            onChange={(event) => set('email', event.target.value)}
            error={errors.email}
          />
          <TextInput
            label="Phone"
            value={values.phone}
            onChange={(event) => set('phone', event.target.value)}
            error={errors.phone}
          />
        </FieldRow>
        <FieldRow>
          <TextInput
            label="Current employer"
            value={values.current_employer}
            onChange={(event) => set('current_employer', event.target.value)}
            error={errors.current_employer}
          />
          <TextInput
            label="Experience (years)"
            inputMode="decimal"
            value={values.total_experience_years}
            onChange={(event) => set('total_experience_years', event.target.value)}
            error={errors.total_experience_years}
          />
        </FieldRow>
        <FieldRow>
          <TextInput
            label="Expected CTC"
            inputMode="decimal"
            value={values.expected_ctc}
            onChange={(event) => set('expected_ctc', event.target.value)}
            error={errors.expected_ctc}
          />
          <TextInput
            label="Notice period (days)"
            type="number"
            value={values.notice_period_days}
            onChange={(event) => set('notice_period_days', event.target.value)}
            error={errors.notice_period_days}
          />
        </FieldRow>

        <div className="rounded-lg border border-line bg-canvas p-3.5">
          <Checkbox
            label="The candidate has consented to us processing their personal data"
            description="Required under the DPDP Act 2023. The API refuses a candidate record without it, and the consent timestamp is recorded."
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />
        </div>
        {errors.consent_given && (
          <Banner tone="danger">{errors.consent_given}</Banner>
        )}
      </div>
    </Modal>
  )
}

/* ------------------------------------------------------------------ list */

/**
 * Two ways to read the same people.
 *
 *   People        — one row per candidate, however many jobs they applied to.
 *   Applications  — one row per application, each carrying its Job Opening,
 *                   stage, status and applied date. This is the view a job's
 *                   "Open in Candidates" link lands on, filtered to that job
 *                   and nothing else, and it is where the job-specific
 *                   questions (job, status, stage, when, experience) live —
 *                   they are properties of an application, not of a person.
 *
 * Both read through the same scoped endpoints; nothing here widens what a
 * role may see.
 */
export function CandidatesPage() {
  const navigate = useNavigate()
  const permissions = usePermissions()
  const list = useListParams({ ordering: '-created_at' })
  const [creating, setCreating] = useState(false)

  // Any application-level filter in the URL means the caller wants the
  // applications view; `view=people` opts back out explicitly.
  const applicationFilters = ['job_opening', 'status', 'current_stage__kind', 'applied_at__gte',
    'candidate__total_experience_years__gte'] as const
  const wantsApplications =
    list.filters.view === 'applications' ||
    (list.filters.view !== 'people' && applicationFilters.some((key) => Boolean(list.filters[key])))

  const mayCreate = permissions.can(RESOURCE.CANDIDATE, ACTION.CREATE)
  // Separate grant, separate button: a role may add a walk-in candidate without
  // being entitled to ingest a platform export of two thousand.
  const mayImport = permissions.can(RESOURCE.CANDIDATE, ACTION.IMPORT)

  const { view: _view, ...restParams } = list.queryParams as Record<string, string | undefined>
  void _view
  const query = useCandidates(wantsApplications ? {} : restParams)
  const applications = useApplications(
    wantsApplications
      ? { ...restParams, ordering: restParams.ordering === '-created_at' ? '-applied_at' : restParams.ordering }
      : {},
  )
  const jobs = useJobs({ page_size: '200' })
  const rows = query.data?.data ?? []
  const applicationRows = applications.data?.data ?? []
  const jobOptions = (jobs.data?.data ?? []).map((job) => ({ value: job.id, label: job.title }))
  const selectedJob = (jobs.data?.data ?? []).find((job) => job.id === list.filters.job_opening)

  const applicationColumns: Array<Column<Application>> = [
    {
      key: 'candidate',
      header: 'Candidate',
      sortKey: 'candidate__first_name',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.candidate_name} size="sm" />
          <p className="truncate font-medium text-ink">{row.candidate_name}</p>
        </div>
      ),
    },
    {
      key: 'job',
      header: 'Job opening',
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate text-ink">{row.job_title}</p>
          <p className="truncate text-xs text-ink-muted">{row.department_name}</p>
        </div>
      ),
    },
    { key: 'stage', header: 'Stage', secondary: true, render: (row) => row.stage_name },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <ApplicationStatusBadge status={row.status} />,
    },
    {
      key: 'verified',
      header: 'HR verified',
      secondary: true,
      render: (row) =>
        row.is_verified ? <Badge tone="success">Verified</Badge> : <span className="text-xs text-ink-subtle">Pending</span>,
    },
    {
      key: 'applied',
      header: 'Applied',
      sortKey: 'applied_at',
      align: 'right',
      render: (row) => <span className="text-xs text-ink-muted">{formatDate(row.applied_at)}</span>,
    },
  ]

  const columns: Array<Column<Candidate>> = [
    {
      key: 'name',
      header: 'Candidate',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.full_name} size="sm" />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.full_name}</p>
            <p className="truncate text-xs text-ink-muted">{row.email}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'applied_for',
      header: 'Applied for',
      render: (row) =>
        row.applications?.length ? (
          <div className="min-w-0 space-y-0.5">
            {row.applications.slice(0, 2).map((application) => (
              <p key={application.id} className="truncate text-sm text-ink">
                {application.job_title}
                <span className="text-xs text-ink-muted"> · {application.stage_name}</span>
              </p>
            ))}
            {row.applications.length > 2 && (
              <p className="text-xs text-ink-subtle">+{row.applications.length - 2} more</p>
            )}
          </div>
        ) : (
          <span className="text-xs text-ink-subtle">No application yet</span>
        ),
    },
    { key: 'phone', header: 'Phone', secondary: true, render: (row) => row.phone || '—' },
    {
      key: 'experience',
      header: 'Experience',
      align: 'right',
      secondary: true,
      render: (row) => (row.total_experience_years ? `${row.total_experience_years} yrs` : '—'),
    },
    {
      key: 'retention',
      header: 'Retention',
      secondary: true,
      render: (row) =>
        row.retention_until ? (
          <Badge tone="warning">Until {formatDate(row.retention_until)}</Badge>
        ) : (
          <span className="text-xs text-ink-subtle">Active</span>
        ),
    },
    {
      key: 'added',
      header: 'Added',
      sortKey: 'created_at',
      align: 'right',
      render: (row) => <span className="text-xs text-ink-muted">{formatDate(row.created_at)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title={selectedJob && wantsApplications ? `Candidates · ${selectedJob.title}` : 'Candidates'}
        description={
          wantsApplications
            ? 'One row per application, each against its job opening. Filter by job to see only that pipeline.'
            : 'People who have applied. A candidate is visible while they have an application you can see.'
        }
        actions={
          <div className="flex gap-2">
            {mayImport && (
              <Button onClick={() => navigate('/recruitment/candidates/import')}>
                Import from a platform
              </Button>
            )}
            {mayCreate && (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Add candidate
              </Button>
            )}
          </div>
        }
      />

      <Section>
        <div className="space-y-4">
          <TableToolbar
            search={list.searchDraft}
            onSearchChange={list.setSearchDraft}
            searchPlaceholder="Search name, email or phone…"
            onReset={list.reset}
            hasFilters={list.hasFilters}
          >
            <div role="group" aria-label="View" className="flex rounded-lg border border-line p-0.5">
              {(['people', 'applications'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={wantsApplications === (option === 'applications')}
                  onClick={() => list.setParam('view', option)}
                  className={
                    'rounded-md px-2.5 py-1 text-xs font-medium ' +
                    (wantsApplications === (option === 'applications')
                      ? 'bg-brand text-white'
                      : 'text-ink-muted hover:text-ink')
                  }
                >
                  {option === 'people' ? 'People' : 'Applications'}
                </button>
              ))}
            </div>
            {wantsApplications && (
              <>
                <FilterSelect
                  label="Job opening"
                  allLabel="All jobs"
                  value={list.filters.job_opening ?? ''}
                  onChange={(value) => list.setParam('job_opening', value)}
                  options={jobOptions}
                />
                <FilterSelect
                  label="Application status"
                  allLabel="Any status"
                  value={list.filters.status ?? ''}
                  onChange={(value) => list.setParam('status', value)}
                  options={[
                    { value: 'active', label: 'In progress' },
                    { value: 'selected', label: 'Selected' },
                    { value: 'offer_sent', label: 'Offer sent' },
                    { value: 'offer_accepted', label: 'Offer accepted' },
                    { value: 'offer_declined', label: 'Offer declined' },
                    { value: 'hired', label: 'Hired' },
                    { value: 'rejected', label: 'Rejected' },
                    { value: 'withdrawn', label: 'Withdrawn' },
                  ]}
                />
                <FilterSelect
                  label="Recruitment stage"
                  allLabel="Any stage"
                  value={list.filters.current_stage__kind ?? ''}
                  onChange={(value) => list.setParam('current_stage__kind', value)}
                  options={[
                    { value: 'application', label: 'Application' },
                    { value: 'hr_verification', label: 'HR verification' },
                    { value: 'interview', label: 'Interview' },
                    { value: 'department_decision', label: 'Department decision' },
                    { value: 'hr_final_decision', label: 'HR final decision' },
                    { value: 'offer', label: 'Offer' },
                    { value: 'onboarding', label: 'Onboarding' },
                    { value: 'terminal', label: 'Closed' },
                  ]}
                />
                <FilterSelect
                  label="Applied"
                  allLabel="Any time"
                  value={list.filters.applied_at__gte ?? ''}
                  onChange={(value) => list.setParam('applied_at__gte', value)}
                  options={[7, 30, 90].map((days) => ({
                    value: new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10),
                    label: `Last ${days} days`,
                  }))}
                />
                <FilterSelect
                  label="Experience"
                  allLabel="Any experience"
                  value={list.filters.candidate__total_experience_years__gte ?? ''}
                  onChange={(value) => list.setParam('candidate__total_experience_years__gte', value)}
                  options={[
                    { value: '1', label: '1+ years' },
                    { value: '3', label: '3+ years' },
                    { value: '5', label: '5+ years' },
                  ]}
                />
              </>
            )}
          </TableToolbar>

          {wantsApplications ? (
            applications.isError ? (
              <ErrorState error={applications.error} onRetry={() => void applications.refetch()} />
            ) : (
              <DataTable
                caption="Applications"
                columns={applicationColumns}
                rows={applicationRows}
                rowKey={(row) => row.id}
                onRowClick={(row) => navigate(`/recruitment/applications/${row.id}`)}
                sort={list.sort}
                onSortChange={list.setSort}
                isLoading={applications.isLoading}
                loadingState={<TableSkeleton columns={6} />}
                emptyState={
                  <EmptyState
                    title={list.hasFilters ? 'No applications match' : 'No applications yet'}
                    description="Applications you can see, each against its job opening."
                  />
                }
                footer={
                  <CursorPager
                    hasPrevious={Boolean(applications.data?.meta.previous)}
                    hasNext={Boolean(applications.data?.meta.next)}
                    onPrevious={() => list.setCursor(applications.data?.meta.previous ?? '')}
                    onNext={() => list.setCursor(applications.data?.meta.next ?? '')}
                    isFetching={applications.isFetching}
                  />
                }
              />
            )
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Candidates"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/recruitment/candidates/${row.id}`)}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={6} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No candidates match' : 'No candidates yet'}
                  description={
                    mayCreate
                      ? 'Add a candidate, then attach them to a published job to start their pipeline.'
                      : 'Candidates linked to applications you can see will appear here.'
                  }
                />
              }
              footer={
                <CursorPager
                  hasPrevious={Boolean(query.data?.meta.previous)}
                  hasNext={Boolean(query.data?.meta.next)}
                  onPrevious={() => list.setCursor(query.data?.meta.previous ?? '')}
                  onNext={() => list.setCursor(query.data?.meta.next ?? '')}
                  isFetching={query.isFetching}
                />
              }
            />
          )}
        </div>
      </Section>

      <CandidateFormModal open={creating} onClose={() => setCreating(false)} />
    </>
  )
}

/* --------------------------------------------------------------- profile */

function ApplyModal({
  open,
  onClose,
  candidateId,
}: {
  open: boolean
  onClose: () => void
  candidateId: string
}) {
  const toast = useToast()
  const jobs = useJobs({ status: 'published' })
  const apply = useCreateApplication()
  const [job, setJob] = useState('')
  const [error, setError] = useState<string | undefined>()

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={apply.isPending}
      title="Add to a job pipeline"
      description="The candidate enters at the workflow's first stage."
      size="sm"
      footer={
        <>
          <Button onClick={onClose} disabled={apply.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!job}
            loading={apply.isPending}
            onClick={() => {
              setError(undefined)
              apply.mutate(
                { candidate: candidateId, job_opening: job },
                {
                  onSuccess: () => {
                    toast.success('Application created')
                    onClose()
                  },
                  onError: (submitError) => {
                    if (submitError instanceof ApiError) setError(submitError.displayMessage)
                    toast.fromError(submitError, 'Could not create the application')
                  },
                },
              )
            }}
          >
            Add to pipeline
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        <Select
          label="Published job"
          required
          placeholder="Select a job"
          value={job}
          onChange={(event) => setJob(event.target.value)}
          options={(jobs.data?.data ?? []).map((item) => ({
            value: item.id,
            label: `${item.title} — ${item.department_name}`,
          }))}
          description="A candidate can only hold one application per job."
        />
      </div>
    </Modal>
  )
}

export function CandidateProfilePage() {
  const { id } = useParams<{ id: string }>()
  const permissions = usePermissions()
  const candidate = useCandidate(id)
  const history = useCandidateHistory(id)
  const [applying, setApplying] = useState(false)

  if (candidate.isLoading) return <LoadingBlock label="Loading candidate" />
  if (candidate.isError) return <ErrorState error={candidate.error} />
  if (!candidate.data) return <EmptyState title="Candidate not found" />

  const record = candidate.data
  const applications = history.data?.applications ?? []

  return (
    <>
      <PageHeader
        breadcrumbs={
          <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
            <Link to="/recruitment/candidates" className="hover:text-ink hover:underline">
              Candidates
            </Link>
            <span className="px-1.5">/</span>
            <span className="text-ink-muted">{record.full_name}</span>
          </nav>
        }
        title={
          <span className="flex items-center gap-3">
            <Avatar name={record.full_name} size="md" />
            {record.full_name}
          </span>
        }
        description={record.email}
        actions={
          permissions.can(RESOURCE.APPLICATION, ACTION.CREATE) && (
            <Button variant="primary" onClick={() => setApplying(true)}>
              Add to a job
            </Button>
          )
        }
      />

      {record.retention_until && (
        <Banner tone="warning" title="Data retention">
          A final decision was recorded on {formatDate(record.final_decision_at)}. This candidate's
          personal data is retained until {formatDate(record.retention_until)}, after which it is
          queued for review before anonymisation. Candidates who became employees are never purged.
        </Banner>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {applications.length === 0 ? (
            <Card>
              <CardHeader title="Applications" />
              <EmptyState compact title="Not attached to any job yet" />
            </Card>
          ) : (
            applications.map((application, index) => (
              <Card key={index} className="space-y-4">
                <CardHeader
                  title={application.job}
                  description={`Currently at ${application.current_stage}`}
                  action={<ApplicationStatusBadge status={application.status} />}
                />

                {application.rejection && (
                  <Banner
                    tone={application.rejection.is_overridden ? 'warning' : 'danger'}
                    title={
                      application.rejection.is_overridden
                        ? 'Rejected, later set aside by an administrator'
                        : `Rejected by ${application.rejection.rejected_by_email}`
                    }
                  >
                    <p className="whitespace-pre-wrap">{application.rejection.reason}</p>
                  </Banner>
                )}

                {application.interviews.length > 0 && (
                  <div className="space-y-2 border-t border-line pt-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      Interviews
                    </p>
                    {application.interviews.map((interview, interviewIndex) => (
                      <div
                        key={interviewIndex}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line p-2.5"
                      >
                        <div className="min-w-0">
                          <p className="text-sm text-ink">{interview.stage}</p>
                          <p className="text-xs text-ink-muted">
                            {interview.interviewer} · {formatDateTime(interview.scheduled_at)}
                          </p>
                        </div>
                        {interview.recommendation && (
                          <RecommendationBadge value={interview.recommendation} />
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {application.events.length > 0 && (
                  <div className="space-y-2 border-t border-line pt-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      History
                    </p>
                    <Timeline
                      entries={application.events.slice(-6).map((event) => ({
                        id: event.id,
                        title: humanize(event.kind),
                        timestamp: formatDateTime(event.created_at),
                        meta: event.actor_label || undefined,
                        body: event.note || undefined,
                      }))}
                    />
                  </div>
                )}
              </Card>
            ))
          )}
        </div>

        <CandidateFormDetails candidate={record} title="Details" />
      </div>

      {id && <ApplyModal open={applying} onClose={() => setApplying(false)} candidateId={id} />}
    </>
  )
}
