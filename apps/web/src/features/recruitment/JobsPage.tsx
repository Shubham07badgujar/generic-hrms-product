/** Job openings: list, create/edit, publish/close, and the detail view. */

import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Card, CardHeader, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, LoadingBlock, TableSkeleton } from '@/components/ui/States'
import { JobStatusBadge, Badge } from '@/components/ui/Badge'
import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { FieldRow, Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { WorkflowStepper } from './WorkflowStepper'
import {
  useApplications,
  useDepartments,
  useDesignations,
  useEmployeeLevels,
  useJob,
  useJobLifecycle,
  useJobs,
  useLocations,
  useRoles,
  useSaveJob,
  useWorkflow,
  useWorkflows,
} from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import type { JobOpening } from '@/lib/types'

const STATUS_OPTIONS = [
  { value: 'draft', label: 'Draft' },
  { value: 'published', label: 'Published' },
  { value: 'on_hold', label: 'On hold' },
  { value: 'closed', label: 'Closed' },
  { value: 'filled', label: 'Filled' },
]

/* ------------------------------------------------------------------ form */

function JobFormModal({
  open,
  onClose,
  job,
}: {
  open: boolean
  onClose: () => void
  job?: JobOpening
}) {
  const toast = useToast()
  const save = useSaveJob(job?.id)

  const workflows = useWorkflows()
  const departments = useDepartments()
  const designations = useDesignations()
  const locations = useLocations()
  const levels = useEmployeeLevels()
  const roles = useRoles()

  const [values, setValues] = useState({
    title: '',
    workflow: '',
    department: '',
    target_role: '',
    designation: '',
    location: '',
    level: '',
    openings_count: '1',
    employment_type: 'full_time',
    age_limit: '',
    gender_preference: 'any',
    salary: '',
    description: '',
    requirements: '',
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!open) return
    setErrors({})
    setValues({
      title: job?.title ?? '',
      workflow: job?.workflow ?? '',
      department: job?.department ?? '',
      target_role: job?.target_role ?? '',
      designation: job?.designation ?? '',
      location: job?.location ?? '',
      level: job?.level ?? '',
      openings_count: String(job?.openings_count ?? 1),
      employment_type: job?.employment_type ?? 'full_time',
      age_limit: job?.age_limit != null ? String(job.age_limit) : '',
      gender_preference: job?.gender_preference || 'any',
      salary: job?.salary ?? '',
      description: job?.description ?? '',
      requirements: job?.requirements ?? '',
    })
  }, [open, job])

  function set(key: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [key]: value }))
    setErrors(({ [key]: _removed, ...rest }) => rest)
  }

  /*
   * The workflow list is filtered by the chosen department's function. The
   * backend's `JobOpening.clean()` refuses a medical workflow on an operations
   * department, so offering the invalid combination only to reject it later is
   * a worse experience than not offering it.
   */
  const chosenDepartment = (departments.data ?? []).find((item) => item.id === values.department)
  const eligibleWorkflows = (workflows.data?.data ?? []).filter(
    (item) =>
      item.is_published &&
      (!item.department_kind || !chosenDepartment || item.department_kind === chosenDepartment.kind),
  )

  /*
   * A designation whose department is set must match the job's department —
   * the backend refuses the combination, so the dropdown never offers it.
   * Designations with no department are usable anywhere.
   */
  const eligibleDesignations = (designations.data ?? []).filter(
    (item) => !item.department || !values.department || item.department === values.department,
  )

  const ready =
    values.title &&
    values.workflow &&
    values.department &&
    values.target_role &&
    values.designation &&
    values.salary

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={save.isPending}
      size="lg"
      title={job ? 'Edit job opening' : 'New job opening'}
      description="The workflow you choose is the only thing that decides this role's hiring pipeline."
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!ready}
            onClick={() => {
              setErrors({})
              save.mutate(
                {
                  title: values.title,
                  workflow: values.workflow,
                  department: values.department,
                  target_role: values.target_role,
                  designation: values.designation || null,
                  location: values.location || null,
                  level: values.level || null,
                  openings_count: Number(values.openings_count) || 1,
                  employment_type: values.employment_type,
                  age_limit: values.age_limit ? Number(values.age_limit) : null,
                  gender_preference: values.gender_preference,
                  salary: values.salary,
                  description: values.description,
                  requirements: values.requirements,
                } as Partial<JobOpening>,
                {
                  onSuccess: () => {
                    toast.success(job ? 'Job updated' : 'Job created as a draft')
                    onClose()
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not save the job')
                  },
                },
              )
            }}
          >
            {job ? 'Save changes' : 'Create draft'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextInput
          label="Job title"
          required
          value={values.title}
          onChange={(event) => set('title', event.target.value)}
          error={errors.title}
        />

        <FieldRow>
          <Select
            label="Department"
            required
            placeholder="Select a department"
            value={values.department}
            onChange={(event) => set('department', event.target.value)}
            options={(departments.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.name} (${item.kind})`,
            }))}
            error={errors.department}
          />
          <Select
            label="Hiring workflow"
            required
            placeholder="Select a workflow"
            value={values.workflow}
            onChange={(event) => set('workflow', event.target.value)}
            options={eligibleWorkflows.map((item) => ({
              value: item.id,
              label: `${item.name} — ${item.stage_count} stages`,
            }))}
            error={errors.workflow}
            description={
              chosenDepartment
                ? `Workflows serving the ${chosenDepartment.kind} function.`
                : 'Choose a department first to narrow this list.'
            }
          />
        </FieldRow>

        <FieldRow>
          <Select
            label="Role on hire"
            required
            placeholder="Select the target role"
            value={values.target_role}
            onChange={(event) => set('target_role', event.target.value)}
            options={(roles.data ?? [])
              .filter((role) => role.is_grantable && role.requires_employee)
              .map((role) => ({ value: role.id, label: role.name }))}
            error={errors.target_role}
            description="Validated against the department when a candidate is converted."
          />
          <Select
            label="Designation"
            required
            placeholder="Select a designation"
            value={values.designation}
            onChange={(event) => set('designation', event.target.value)}
            options={eligibleDesignations.map((item) => ({
              value: item.id,
              label: item.title,
            }))}
            error={errors.designation}
            description={
              values.department
                ? 'Only designations valid for the chosen department are offered.'
                : 'Choose a department first to narrow this list.'
            }
          />
        </FieldRow>

        <FieldRow columns={3}>
          <TextInput
            label="Salary"
            required
            placeholder="e.g. ₹4–6 LPA"
            value={values.salary}
            onChange={(event) => set('salary', event.target.value)}
            error={errors.salary}
          />
          <TextInput
            label="Age limit"
            type="number"
            min={18}
            placeholder="e.g. 35"
            value={values.age_limit}
            onChange={(event) => set('age_limit', event.target.value)}
            error={errors.age_limit}
          />
          <Select
            label="Gender preference"
            value={values.gender_preference}
            onChange={(event) => set('gender_preference', event.target.value)}
            options={[
              { value: 'any', label: 'Any' },
              { value: 'male', label: 'Male' },
              { value: 'female', label: 'Female' },
            ]}
            error={errors.gender_preference}
          />
        </FieldRow>

        <FieldRow columns={3}>
          <Select
            label="Location"
            placeholder="Optional"
            value={values.location}
            onChange={(event) => set('location', event.target.value)}
            options={(locations.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            error={errors.location}
          />
          <Select
            label="Seniority level"
            placeholder="Optional"
            value={values.level}
            onChange={(event) => set('level', event.target.value)}
            options={(levels.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            error={errors.level}
          />
          <TextInput
            label="Openings"
            type="number"
            min={1}
            value={values.openings_count}
            onChange={(event) => set('openings_count', event.target.value)}
            error={errors.openings_count}
          />
        </FieldRow>

        <TextArea
          label="Description"
          rows={4}
          value={values.description}
          onChange={(event) => set('description', event.target.value)}
          error={errors.description}
        />
        <TextArea
          label="Requirements"
          rows={4}
          value={values.requirements}
          onChange={(event) => set('requirements', event.target.value)}
          error={errors.requirements}
        />

        {errors.non_field_errors && (
          <Banner tone="danger" title="The server refused this">
            {errors.non_field_errors}
          </Banner>
        )}
      </div>
    </Modal>
  )
}

/* ------------------------------------------------------------------ list */

export function JobsPage() {
  const navigate = useNavigate()
  const permissions = usePermissions()
  const list = useListParams({ ordering: '-created_at' })
  const query = useJobs(list.queryParams)
  const [creating, setCreating] = useState(false)

  const rows = query.data?.data ?? []
  const mayCreate = permissions.can(RESOURCE.JOB_OPENING, ACTION.CREATE)

  const columns: Array<Column<JobOpening>> = [
    {
      key: 'title',
      header: 'Job',
      sortKey: 'title',
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium text-ink">{row.title}</p>
          <p className="truncate text-xs text-ink-muted">{row.department_name}</p>
        </div>
      ),
    },
    {
      key: 'workflow',
      header: 'Workflow',
      secondary: true,
      render: (row) => <Badge tone="info">{row.workflow_name}</Badge>,
    },
    { key: 'status', header: 'Status', render: (row) => <JobStatusBadge status={row.status} /> },
    {
      key: 'applications',
      header: 'Applicants',
      align: 'right',
      render: (row) => <span className="tabular">{row.application_count}</span>,
    },
    {
      key: 'openings',
      header: 'Openings',
      align: 'right',
      secondary: true,
      render: (row) => <span className="tabular">{row.openings_count}</span>,
    },
    {
      key: 'created',
      header: 'Created',
      sortKey: 'created_at',
      align: 'right',
      secondary: true,
      render: (row) => <span className="text-xs text-ink-muted">{formatDate(row.created_at)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Job openings"
        description="Each job runs the workflow you attach to it — that is the only difference between one pipeline and another."
        actions={
          mayCreate && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              New job
            </Button>
          )
        }
      />

      <Section>
        <div className="space-y-4">
          <TableToolbar
            search={list.searchDraft}
            onSearchChange={list.setSearchDraft}
            searchPlaceholder="Search job titles…"
            onReset={list.reset}
            hasFilters={list.hasFilters}
          >
            <FilterSelect
              label="Status"
              value={list.filters.status ?? ''}
              onChange={(value) => list.setParam('status', value)}
              options={STATUS_OPTIONS}
              allLabel="All statuses"
            />
          </TableToolbar>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Job openings"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/recruitment/jobs/${row.id}`)}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={6} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No jobs match these filters' : 'No job openings yet'}
                  description={
                    mayCreate
                      ? 'Create a job and attach a hiring workflow to start a pipeline.'
                      : 'Job openings you are permitted to see will appear here.'
                  }
                  action={
                    mayCreate && !list.hasFilters ? (
                      <Button variant="primary" onClick={() => setCreating(true)}>
                        New job
                      </Button>
                    ) : undefined
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

      <JobFormModal open={creating} onClose={() => setCreating(false)} />
    </>
  )
}

/* ---------------------------------------------------------------- detail */

/**
 * The public application link, and where it goes.
 *
 * Every published job has one — /apply/<token> on this deployment — and it
 * always leads into THIS job's pipeline. When Google Forms is configured, the
 * form Google built is shown beside it; when it is not, that is said plainly
 * rather than left as an empty field for someone to wonder about.
 */
function ApplicationLinkCard({ job }: { job: JobOpening }) {
  const toast = useToast()
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(job.application_url)
      setCopied(true)
      toast.success('Application link copied')
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Could not copy — select the link and copy it manually')
    }
  }

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Application link"
        description="Share this on job boards and social posts. Every submission lands in this job's pipeline."
      />
      {job.status !== 'published' && (
        <Banner tone="warning">
          {job.status === 'draft'
            ? 'The link is live once the job is published. Until then it tells applicants the position is not accepting applications.'
            : 'This job is no longer published; the link tells applicants it is closed.'}
        </Banner>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          readOnly
          aria-label="Application link"
          value={job.application_url}
          onFocus={(event) => event.currentTarget.select()}
          className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-3 py-2 font-mono text-xs text-ink"
        />
        <Button onClick={() => void copy()} variant="primary">
          {copied ? 'Copied' : 'Copy Application Link'}
        </Button>
      </div>
      <div className="space-y-1 text-xs text-ink-muted">
        {job.external_form_provider === 'google_forms' && job.external_form_url ? (
          <p>
            Google Form:{' '}
            <a
              href={job.external_form_url}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-brand hover:underline"
            >
              open form
            </a>
            {job.external_form_synced_at
              ? ` · responses last pulled ${formatDate(job.external_form_synced_at)}`
              : ' · responses are pulled every 10 minutes'}
          </p>
        ) : job.external_form_error ? (
          <p className="text-danger">
            Google Form could not be created — {job.external_form_error.slice(0, 160)}. The hosted
            link above works regardless.
          </p>
        ) : (
          <p>
            Applications are collected on the HRMS-hosted form. A Google Form is created
            automatically when Google Forms integration is configured for this deployment.
          </p>
        )}
        <p>
          Asks {job.resolved_application_fields?.length ?? 0} questions
          {job.application_fields?.length ? ' (customised for this job)' : ' (standard set)'}.
        </p>
      </div>
    </Card>
  )
}

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>()
  const permissions = usePermissions()
  const toast = useToast()
  const navigate = useNavigate()

  const job = useJob(id)
  const workflow = useWorkflow(job.data?.workflow)
  const applications = useApplications({ job_opening: id })
  const lifecycle = useJobLifecycle(id!)

  const [editing, setEditing] = useState(false)
  const [closing, setClosing] = useState(false)

  if (job.isLoading) return <LoadingBlock label="Loading job" />
  if (job.isError) return <ErrorState error={job.error} />
  if (!job.data) return <EmptyState title="Job not found" />

  const record = job.data
  const mayEdit = permissions.can(RESOURCE.JOB_OPENING, ACTION.EDIT)
  const applicationRows = applications.data?.data ?? []

  return (
    <>
      <PageHeader
        breadcrumbs={
          <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
            <Link to="/recruitment/jobs" className="hover:text-ink hover:underline">
              Job openings
            </Link>
            <span className="px-1.5">/</span>
            <span className="text-ink-muted">{record.title}</span>
          </nav>
        }
        title={record.title}
        description={`${record.department_name} · ${record.workflow_name}`}
        meta={
          <>
            <JobStatusBadge status={record.status} />
            <span className="text-xs text-ink-subtle">
              {record.application_count} applicants · {record.openings_count} openings
            </span>
          </>
        }
        actions={
          mayEdit && (
            <>
              <Button onClick={() => setEditing(true)}>Edit</Button>
              {record.status === 'draft' && (
                <Button
                  variant="primary"
                  loading={lifecycle.isPending}
                  onClick={() =>
                    lifecycle.mutate('publish', {
                      onSuccess: () => toast.success('Job published'),
                      onError: (error) => toast.fromError(error, 'Could not publish'),
                    })
                  }
                >
                  Publish
                </Button>
              )}
              {record.status !== 'closed' && record.status !== 'draft' && (
                <Button variant="danger-soft" onClick={() => setClosing(true)}>
                  Close job
                </Button>
              )}
            </>
          )
        }
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <Card className="space-y-4">
            <CardHeader title="Details" />
            <DescriptionList
              columns={2}
              items={[
                { label: 'Department', value: record.department_name },
                { label: 'Workflow', value: record.workflow_name },
                { label: 'Role on hire', value: record.target_role_code },
                { label: 'Employment type', value: record.employment_type },
                { label: 'Salary', value: record.salary || '—' },
                { label: 'Age limit', value: record.age_limit != null ? `Up to ${record.age_limit}` : '—' },
                {
                  label: 'Gender preference',
                  value:
                    record.gender_preference === 'male'
                      ? 'Male'
                      : record.gender_preference === 'female'
                        ? 'Female'
                        : 'Any',
                },
                { label: 'Published', value: formatDate(record.published_at) },
                { label: 'Closed', value: formatDate(record.closed_at) },
              ]}
            />
            {record.description && (
              <div className="space-y-1 border-t border-line pt-4">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                  Description
                </p>
                <p className="whitespace-pre-wrap text-sm text-ink-muted">{record.description}</p>
              </div>
            )}
            {record.requirements && (
              <div className="space-y-1 border-t border-line pt-4">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                  Requirements
                </p>
                <p className="whitespace-pre-wrap text-sm text-ink-muted">{record.requirements}</p>
              </div>
            )}
          </Card>

          {mayEdit && <ApplicationLinkCard job={record} />}

          <Card className="space-y-4">
            <CardHeader
              title="Applicants"
              description="Candidates in this job's pipeline — and nobody else's."
              action={
                <Link
                  to={`/recruitment/candidates?job_opening=${record.id}`}
                  className="text-xs font-medium text-brand hover:underline"
                >
                  Open in Candidates
                </Link>
              }
            />
            {applications.isLoading ? (
              <LoadingBlock />
            ) : applicationRows.length === 0 ? (
              <EmptyState compact title="No applicants yet" />
            ) : (
              <ul className="divide-y divide-line">
                {applicationRows.map((application) => (
                  <li key={application.id}>
                    <Link
                      to={`/recruitment/applications/${application.id}`}
                      className="flex items-center justify-between gap-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-ink">
                          {application.candidate_name}
                        </p>
                        <p className="text-xs text-ink-muted">
                          {application.stage_name} · applied {formatDate(application.applied_at)}
                        </p>
                      </div>
                      <Badge tone="info">{application.status.replace(/_/g, ' ')}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <Card className="space-y-4">
          <CardHeader
            title="Hiring pipeline"
            description="Read from the workflow configuration, not written into this screen."
          />
          {workflow.isLoading ? (
            <LoadingBlock />
          ) : workflow.data ? (
            <WorkflowStepper workflow={workflow.data} />
          ) : (
            <EmptyState compact title="Workflow unavailable" />
          )}
        </Card>
      </div>

      <JobFormModal open={editing} onClose={() => setEditing(false)} job={record} />

      <ConfirmDialog
        open={closing}
        onClose={() => setClosing(false)}
        loading={lifecycle.isPending}
        tone="danger"
        title="Close this job"
        description="No new applications will be accepted. Existing applications continue through their workflow."
        confirmLabel="Close job"
        onConfirm={() =>
          lifecycle.mutate('close', {
            onSuccess: () => {
              toast.success('Job closed')
              setClosing(false)
              navigate('/recruitment/jobs')
            },
            onError: (error) => toast.fromError(error, 'Could not close the job'),
          })
        }
      />
    </>
  )
}
