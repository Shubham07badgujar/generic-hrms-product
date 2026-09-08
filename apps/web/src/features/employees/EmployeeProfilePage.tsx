/**
 * The employee profile.
 *
 * One request (`/employees/{id}/profile/`) returns every section the caller is
 * permitted to read, and the tab strip is built from what came back. A tab for
 * a withheld section is still shown — but its panel explains that the section
 * was not returned, rather than rendering a convincing empty table.
 *
 * Sensitive identifiers arrive already masked from the API. There is no
 * unmasking here and no client-side redaction to get wrong.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Card, CardHeader, DescriptionList, PageHeader } from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Avatar, Banner, Tabs, TabPanel, Timeline } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Select, TextInput } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { roleLabel } from '@/app/AppShell'
import { useAuth, usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useChangeEmployeeStatus,
  useEmployeeProfile,
} from '@/lib/lifecycleQueries'
import { useMyEmployeeRecord, useRemoveEmployee } from '@/lib/queries'
import { CompensationSection } from '@/features/payroll/CompensationSection'
import { ApiError } from '@/lib/api'
import { formatDate, humanize } from '@/lib/format'
import {
  AssetsSection,
  buildProfileTimeline,
  CompanyAccountSection,
  DocumentsSection,
  LettersSection,
  OnboardingSection,
  ProbationSection,
  RecruitmentSourceCard,
  SectionStack,
} from './ProfileSections'
import type { EmployeeStatus, UUID } from '@/lib/types'

const STATUS_TONES: Record<EmployeeStatus, Tone> = {
  onboarding: 'info',
  on_probation: 'warning',
  active: 'success',
  confirmed: 'success',
  on_leave: 'info',
  on_notice: 'warning',
  resigned: 'neutral',
  terminated: 'danger',
  exited: 'neutral',
}

/**
 * Ending employment demands a reason; the softer moves do not.
 * Mirrors `REASON_REQUIRED` in the lifecycle service.
 */
const REASON_REQUIRED: EmployeeStatus[] = ['resigned', 'terminated', 'exited']

function StatusChanger({
  employeeId,
  current,
  allowed,
  onChanged,
}: {
  employeeId: UUID
  current: EmployeeStatus
  allowed: EmployeeStatus[]
  onChanged: () => void
}) {
  const toast = useToast()
  const change = useChangeEmployeeStatus(employeeId)
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<EmployeeStatus | ''>('')
  const [error, setError] = useState<string | undefined>()

  // The server sends the permitted next states, so the picker can never offer
  // a move the API would refuse.
  if (!allowed.length) return null

  const needsReason = target !== '' && REASON_REQUIRED.includes(target)

  function submit(reason: string) {
    setError(undefined)
    change.mutate(
      { status: target as string, reason },
      {
        onSuccess: () => {
          toast.success(`Status changed to ${humanize(target as string).toLowerCase()}`)
          setOpen(false)
          setTarget('')
          onChanged()
        },
        onError: (submitError) => {
          if (submitError instanceof ApiError) setError(submitError.displayMessage)
          toast.fromError(submitError, 'Could not change the status')
        },
      },
    )
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>Change status</Button>

      <ReasonDialog
        open={open}
        onClose={() => setOpen(false)}
        loading={change.isPending}
        serverError={error}
        tone={needsReason ? 'danger' : 'primary'}
        title="Change employment status"
        description={`Currently ${humanize(current).toLowerCase()}.`}
        label={needsReason ? 'Reason' : 'Note (optional)'}
        confirmLabel="Change status"
        onSubmit={submit}
        banner={
          <>
            <Select
              label="New status"
              required
              placeholder="Select a status"
              value={target}
              onChange={(event) => setTarget(event.target.value as EmployeeStatus)}
              options={allowed.map((value) => ({
                value,
                label: humanize(value),
              }))}
              description="Only transitions the workflow permits from the current status are listed."
            />
            {target && !needsReason && (
              <Banner tone="info" className="mt-3">
                This move does not require a reason, but anything you write is recorded.
              </Banner>
            )}
            {needsReason && (
              <Banner tone="warning" className="mt-3" title="This ends employment">
                A written reason is required and is stored permanently in the audit trail. An
                exit is refused while company property is still allocated.
              </Banner>
            )}
          </>
        }
      />
    </>
  )
}

/**
 * HR sets PAN / Aadhaar / UAN / bank details. Only filled fields are sent;
 * reads stay masked, so the placeholders show what is on file without ever
 * revealing it. Gated to organisation-wide employee-edit (HR/Admin) — a
 * changed bank account is where salary lands.
 */
function ChangeNameAction({
  employee,
  onChanged,
}: {
  employee: {
    id: UUID
    first_name: string
    middle_name?: string | null
    last_name?: string | null
    full_name: string
  }
  onChanged: () => void
}) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [form, setForm] = useState({
    first_name: employee.first_name ?? '',
    middle_name: employee.middle_name ?? '',
    last_name: employee.last_name ?? '',
    reason: '',
  })

  const preview = [form.first_name, form.middle_name, form.last_name]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' ')

  async function submit() {
    setBusy(true)
    setErrors({})
    try {
      const { apiPatch } = await import('@/lib/api')
      await apiPatch(`/employees/${employee.id}/name/`, {
        first_name: form.first_name,
        middle_name: form.middle_name,
        last_name: form.last_name,
        reason: form.reason,
      })
      toast.success('Name updated', `This record now reads ${preview}.`)
      setOpen(false)
      onChanged()
    } catch (error) {
      if (error instanceof ApiError) setErrors(error.fieldErrors)
      toast.fromError(error, 'Could not update the name')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        Edit name
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        busy={busy}
        title="Edit name"
        description="Corrects the name everywhere it appears — the directory, payslips and future letters. The employee code and sign-in email are not affected."
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={busy}
              disabled={!form.first_name.trim()}
              onClick={() => void submit()}
            >
              Save name
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <TextInput
              label="First name"
              required
              value={form.first_name}
              onChange={(event) => setForm({ ...form, first_name: event.target.value })}
              error={errors.first_name}
            />
            <TextInput
              label="Middle name"
              value={form.middle_name}
              onChange={(event) => setForm({ ...form, middle_name: event.target.value })}
              error={errors.middle_name}
              description="Optional"
            />
            <TextInput
              label="Last name"
              value={form.last_name}
              onChange={(event) => setForm({ ...form, last_name: event.target.value })}
              error={errors.last_name}
              description="Optional"
            />
          </div>
          {preview && preview !== employee.full_name && (
            <p className="text-sm text-ink-subtle">
              Will read as <strong className="text-ink">{preview}</strong>
            </p>
          )}
          <TextInput
            label="Reason"
            value={form.reason}
            onChange={(event) => setForm({ ...form, reason: event.target.value })}
            placeholder="e.g. Married name, or a spelling correction"
            description="Kept with the change in the record's history."
          />
        </div>
      </Modal>
    </>
  )
}


function ResendCredentialsAction({
  employee,
}: {
  employee: { id: UUID; full_name: string; personal_email?: string | null }
}) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    try {
      const { apiPost } = await import('@/lib/api')
      const result = await apiPost<{ sent: boolean; recipient: string; detail: string }>(
        `/employees/${employee.id}/resend-credentials/`,
        {},
      )
      if (result.sent) {
        toast.success('New sign-in details sent', result.detail)
      } else {
        toast.error('The email did not send', result.detail)
      }
      setOpen(false)
    } catch (error) {
      toast.fromError(error, 'Could not reissue credentials')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        Resend sign-in details
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        busy={busy}
        title="Resend sign-in details"
        description={`A fresh temporary password will be generated and emailed to ${
          employee.personal_email || 'their personal address'
        }.`}
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void submit()}>
              Send new details
            </Button>
          </>
        }
      >
        <Banner tone="warning" title="Their current password will stop working">
          Use this when someone never received their welcome email — a mail provider can accept
          a message and then decide not to deliver it, which nothing here can see. A new
          temporary password is issued because the original is stored nowhere, and reissuing
          also invalidates anything that leaked through a bounced copy. They will be asked to
          choose their own password on first sign-in, as usual.
        </Banner>
      </Modal>
    </>
  )
}


function ChangeReportingManagerAction({
  employee,
  onChanged,
}: {
  employee: { id: UUID; full_name: string; reporting_manager: UUID | null; roles: string[] }
  onChanged: () => void
}) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [choice, setChoice] = useState<string>(employee.reporting_manager ?? '')
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [options, setOptions] = useState<
    { id: string; full_name: string; employee_code: string; department_name: string }[]
  >([])
  const [loading, setLoading] = useState(false)

  // The eligible list comes from the SERVER, computed by the same function
  // the save validates against — so the dropdown can never offer a choice
  // that is then refused, and never hide one that would be accepted.
  useEffect(() => {
    if (!open) return
    const role = employee.roles[0]
    if (!role) return
    setLoading(true)
    void (async () => {
      try {
        const { apiGet } = await import('@/lib/api')
        setOptions(
          await apiGet(
            `/employees/eligible-managers/?role_code=${encodeURIComponent(role)}&employee=${employee.id}`,
          ),
        )
      } catch {
        setOptions([])
      } finally {
        setLoading(false)
      }
    })()
  }, [open, employee.id, employee.roles])

  async function submit() {
    setBusy(true)
    setError('')
    try {
      const { apiPatch } = await import('@/lib/api')
      await apiPatch(`/employees/${employee.id}/reporting-manager/`, {
        reporting_manager: choice || null,
        reason,
      })
      toast.success('Reporting line updated')
      setOpen(false)
      onChanged()
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.fieldErrors.reporting_manager ?? err.message)
      }
      toast.fromError(err, 'Could not update the reporting line')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        Change
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        busy={busy}
        title="Change reporting manager"
        description="Who approves this person's leave and sees them in their reporting tree. A manager may be at the same level or higher; the list already excludes anyone who reports to this person."
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void submit()}>
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select
            label="Reporting manager"
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
            placeholder={loading ? 'Loading eligible people…' : 'No manager'}
            options={options.map((row) => ({
              value: row.id,
              label: `${row.full_name} — ${row.employee_code}${
                row.department_name ? ` · ${row.department_name}` : ''
              }`,
            }))}
            error={error}
            description={
              loading
                ? undefined
                : `${options.length} eligible — same level or higher, any department.`
            }
          />
          <TextInput
            label="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="e.g. Moved to the clinical side"
            description="Recorded in the audit trail alongside the change."
          />
        </div>
      </Modal>
    </>
  )
}


function UpdateIdentifiersAction({
  employee,
  onChanged,
}: {
  employee: {
    id: UUID
    pan: string
    aadhaar: string
    uan: string
    esic_number: string
    bank_account_number: string
    bank_ifsc: string
    bank_name: string
  }
  onChanged: () => void
}) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const empty = {
    pan: '', aadhaar: '', uan: '', esic_number: '',
    bank_account_number: '', bank_ifsc: '', bank_name: '',
  }
  const [form, setForm] = useState(empty)

  const FIELDS: Array<[keyof typeof empty, string, string]> = [
    ['pan', 'PAN', 'AAAAA9999A'],
    ['aadhaar', 'Aadhaar', '12 digits'],
    ['uan', 'UAN', '12 digits'],
    ['esic_number', 'ESIC number', '10–17 digits'],
    ['bank_account_number', 'Bank account number', '9–18 digits'],
    ['bank_ifsc', 'IFSC', 'HDFC0001234'],
    ['bank_name', 'Bank name', ''],
  ]

  async function submit() {
    const body = Object.fromEntries(
      Object.entries(form).filter(([, value]) => value.trim() !== ''),
    )
    if (Object.keys(body).length === 0) {
      setOpen(false)
      return
    }
    setBusy(true)
    setErrors({})
    try {
      const { apiPatch } = await import('@/lib/api')
      await apiPatch(`/employees/${employee.id}/identifiers/`, body)
      toast.success('Identifiers updated', 'Stored encrypted; shown masked.')
      setForm(empty)
      setOpen(false)
      onChanged()
    } catch (error) {
      if (error instanceof ApiError) setErrors(error.fieldErrors)
      toast.fromError(error, 'Could not update')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        Update
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        busy={busy}
        size="md"
        title="Update statutory and bank identifiers"
        description="Fill only what you are setting or correcting — empty fields are left as they are. Values are stored encrypted, shown masked everywhere, and every change is audited."
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
            <Button variant="primary" loading={busy} onClick={() => void submit()}>
              Save identifiers
            </Button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map(([key, label, hint]) => (
            <TextInput
              key={key}
              label={label}
              value={form[key]}
              placeholder={(employee[key] as string) || hint}
              onChange={(event) => setForm({ ...form, [key]: event.target.value })}
              error={errors[key]}
              description={employee[key] ? `On file: ${employee[key]}` : undefined}
            />
          ))}
        </div>
      </Modal>
    </>
  )
}

export function EmployeeProfileView({ employeeId }: { employeeId: UUID | undefined }) {
  const permissions = usePermissions()
  const { user: viewer } = useAuth()
  // The server restricts reporting-line edits to organisation-wide
  // EMPLOYEE/EDIT; mirror that exactly so the button is not offered to
  // someone the API will refuse.
  const mayEditPeople = permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.EDIT, 'all')
  const profile = useEmployeeProfile(employeeId)
  const [tab, setTab] = useState('overview')

  const timeline = useMemo(
    () => (profile.data ? buildProfileTimeline(profile.data) : []),
    [profile.data],
  )

  if (profile.isLoading) return <LoadingBlock label="Loading profile" />
  if (profile.isError) return <ErrorState error={profile.error} />
  if (!profile.data) return <EmptyState title="Employee not found" />

  const data = profile.data
  const employee = data.employee
  const isOwnRecord = viewer?.employee_id === employee.id
  const refresh = () => void profile.refetch()

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'employment', label: 'Employment' },
    { id: 'documents', label: 'Documents', count: data.documents?.length },
    {
      id: 'onboarding',
      label: 'Onboarding',
      count: data.onboarding?.outstanding_mandatory_count || undefined,
    },
    { id: 'probation', label: 'Probation' },
    { id: 'assets', label: 'Assets', count: data.asset_allocations?.filter((a) => a.is_open).length },
    // SALARY is scoped server-side: HR/Finance see anyone, an employee only
    // ever gets rows for themselves, so the tab is safe to offer whenever the
    // caller holds the permission at all.
    ...(permissions.can(RESOURCE.SALARY) ? [{ id: 'salary', label: 'Salary' }] : []),
    { id: 'account', label: 'Company account' },
    { id: 'letters', label: 'Letters', count: data.letters?.length },
    { id: 'history', label: 'History' },
  ]

  return (
    <>
      <PageHeader
        breadcrumbs={
          permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.VIEW, 'team') ? (
            <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
              <Link to="/employees" className="hover:text-ink hover:underline">
                Employees
              </Link>
              <span className="px-1.5">/</span>
              <span className="text-ink-muted">{employee.full_name}</span>
            </nav>
          ) : undefined
        }
        title={
          <span className="flex items-center gap-3">
            <Avatar name={employee.full_name} size="md" />
            {employee.full_name}
            {/*
              HR maintains anyone's name; everyone maintains their own. This
              mirrors the server, which decides the same thing from the
              existing EMPLOYEE/EDIT grant — organisation-wide for HR, self
              for everyone else.
            */}
            {(mayEditPeople || isOwnRecord) && (
              <ChangeNameAction employee={employee} onChanged={refresh} />
            )}
          </span>
        }
        description={`${employee.employee_code} · ${employee.designation_title || 'No designation'}`}
        meta={
          <>
            <Badge tone={STATUS_TONES[employee.status] ?? 'neutral'} dot>
              {humanize(employee.status)}
            </Badge>
            {employee.probation_status !== 'not_applicable' && (
              <Badge tone={employee.probation_status === 'due' ? 'warning' : 'info'}>
                Probation: {humanize(employee.probation_status)}
              </Badge>
            )}
            {employee.roles.map((code) => (
              <Badge key={code} tone="brand">
                {roleLabel(code)}
              </Badge>
            ))}
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusChanger
              employeeId={employee.id}
              current={employee.status}
              allowed={data.allowed_status_transitions}
              onChanged={refresh}
            />
            {permissions.can(RESOURCE.EMPLOYEE, ACTION.DELETE) &&
              (employee.status === 'exited' ||
                employee.status === 'terminated' ||
                // Admin holds the master key: delete in any status (the server
                // enforces the same split — HR Head must offboard first).
                permissions.hasRole('admin')) && (
                <RemoveEmployeeAction employeeId={employee.id} name={employee.full_name} />
              )}
          </div>
        }
      />

      {employee.status === 'exited' && (
        <Banner tone="neutral" title="This employee has left">
          Their record is retained for statutory and audit purposes. Nothing moves out of the
          exited state — a returning employee gets a new record.
        </Banner>
      )}

      <Tabs active={tab} onChange={setTab} items={tabs} />

      <TabPanel id="overview" active={tab}>
        <div className="grid gap-5 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Personal" />
            <div className="pt-4">
              <DescriptionList
                columns={2}
                items={[
                  { label: 'Full name', value: employee.full_name },
                  { label: 'Work email', value: employee.work_email || '—' },
                  { label: 'Personal email', value: employee.personal_email || '—' },
                  { label: 'Phone', value: employee.phone || '—' },
                  { label: 'Date of birth', value: formatDate(employee.date_of_birth) },
                  { label: 'Gender', value: employee.gender || '—' },
                ]}
              />
            </div>
          </Card>

          <SectionStack>
            <Card>
              <CardHeader title="At a glance" />
              <div className="pt-4">
                <DescriptionList
                  columns={1}
                  items={[
                    { label: 'Department', value: employee.department_name ?? '—' },
                    { label: 'Reports to', value: employee.reporting_manager_name || '—' },
                    { label: 'Joined', value: formatDate(employee.date_of_joining) },
                    {
                      label: 'Probation ends',
                      value: formatDate(employee.probation_end_date),
                    },
                  ]}
                />
              </div>
            </Card>
            <RecruitmentSourceCard profile={data} />
          </SectionStack>
        </div>
      </TabPanel>

      <TabPanel id="employment" active={tab}>
        <SectionStack>
          <Card>
            <CardHeader title="Employment" />
            <div className="pt-4">
              <DescriptionList
                columns={3}
                items={[
                  { label: 'Employee code', value: employee.employee_code },
                  {
                    label: 'Sign-in email',
                    value: (
                      <span className="flex flex-wrap items-center gap-2">
                        {employee.work_email}
                        {mayEditPeople && <ResendCredentialsAction employee={employee} />}
                      </span>
                    ),
                  },
                  { label: 'Department', value: employee.department_name ?? '—' },
                  { label: 'Designation', value: employee.designation_title || '—' },
                  {
                    label: 'Reports to',
                    value: (
                      <span className="flex items-center gap-2">
                        {employee.reporting_manager_name || '—'}
                        {mayEditPeople && (
                          <ChangeReportingManagerAction employee={employee} onChanged={refresh} />
                        )}
                      </span>
                    ),
                  },
                  { label: 'Employment type', value: humanize(employee.employment_type) },
                  { label: 'Status', value: humanize(employee.status) },
                  { label: 'Joined', value: formatDate(employee.date_of_joining) },
                  { label: 'Confirmed', value: formatDate(employee.confirmation_date) },
                  { label: 'Exit date', value: formatDate(employee.date_of_exit) },
                ]}
              />
            </div>
          </Card>

          <Card className="space-y-4">
            <CardHeader
              title="Statutory and banking"
              description="Encrypted at rest and served masked."
              action={
                permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.EDIT, 'all') ? (
                  <UpdateIdentifiersAction employee={employee} onChanged={refresh} />
                ) : undefined
              }
            />
            <Banner tone="info">
              PAN, Aadhaar and bank account numbers are returned already masked by the API.
              There is no unmasked variant on this endpoint — releasing full identifiers is a
              separate, audited path.
            </Banner>
            <DescriptionList
              columns={3}
              items={[
                { label: 'PAN', value: <span className="font-mono">{employee.pan || '—'}</span> },
                {
                  label: 'Aadhaar',
                  value: <span className="font-mono">{employee.aadhaar || '—'}</span>,
                },
                { label: 'UAN', value: employee.uan || '—' },
                { label: 'ESIC number', value: employee.esic_number || '—' },
                {
                  label: 'Bank account',
                  value: <span className="font-mono">{employee.bank_account_number || '—'}</span>,
                },
                { label: 'IFSC', value: employee.bank_ifsc || '—' },
              ]}
            />
          </Card>
        </SectionStack>
      </TabPanel>

      <TabPanel id="documents" active={tab}>
        <DocumentsSection
          employeeId={employee.id}
          documents={data.documents}
          onChanged={refresh}
        />
      </TabPanel>

      <TabPanel id="onboarding" active={tab}>
        <OnboardingSection
          onboarding={data.onboarding}
          employeeId={employee.id}
          onChanged={refresh}
        />
      </TabPanel>

      <TabPanel id="probation" active={tab}>
        <ProbationSection
          reviews={data.probation_reviews}
          employee={employee}
          onChanged={refresh}
        />
      </TabPanel>

      <TabPanel id="assets" active={tab}>
        <SectionStack>
          <AssetsSection
            allocations={data.asset_allocations}
            employeeId={employee.id}
            onboardingStatus={data.onboarding?.status ?? null}
            onChanged={refresh}
          />
        </SectionStack>
      </TabPanel>

      <TabPanel id="salary" active={tab}>
        <CompensationSection employeeId={employee.id} />
      </TabPanel>

      <TabPanel id="account" active={tab}>
        <CompanyAccountSection
          account={data.company_account}
          employeeId={employee.id}
          onChanged={refresh}
        />
      </TabPanel>

      <TabPanel id="letters" active={tab}>
        <LettersSection letters={data.letters} employee={employee} onChanged={refresh} />
      </TabPanel>

      <TabPanel id="history" active={tab}>
        <Card>
          <CardHeader
            title="History"
            description="Assembled from the sections you are permitted to see."
          />
          <div className="pt-4">
            {timeline.length === 0 ? (
              <EmptyState title="Nothing recorded yet" />
            ) : (
              <Timeline entries={timeline} />
            )}
          </div>
        </Card>
      </TabPanel>
    </>
  )
}

export function EmployeeDetailPage() {
  const { id } = useParams<{ id: string }>()
  return <EmployeeProfileView employeeId={id} />
}

/** The signed-in user's own profile, via `/employees/me/`. */
export function MyProfilePage() {
  const mine = useMyEmployeeRecord()
  const { user } = useAuth()

  if (mine.isLoading) return <LoadingBlock label="Loading your profile" />
  if (mine.isError || !mine.data) {
    return (
      <>
        <PageHeader title="My profile" />
        <Card>
          <EmptyState
            title="No employment record"
            description={
              'Your account is a system role — Admin and CEO exist without an employee ' +
              'record by design, so there is nothing to show here.'
            }
          />
        </Card>
      </>
    )
  }

  return (
    <>
      {user?.onboarding_pending && (
        <Banner tone="warning" title="Complete your onboarding">
          Upload your mandatory documents from the Documents tab below — PAN card, Aadhaar card
          and your highest qualification certificate. HR reviews and approves each one; once
          everything required is approved, full HRMS access opens automatically. Your login
          email is <strong>{user.email}</strong> — always sign in with it.
        </Banner>
      )}
      <EmployeeProfileView employeeId={mine.data.id} />
    </>
  )
}


/**
 * Remove an employee from the system — Admin (any status) or HR Head (once
 * exited/terminated). The server soft-deletes the record (payroll, documents
 * and audit history stay), switches the login off, releases the login email
 * so the same person can be onboarded again, and audits the act with the
 * reason given here.
 */
function RemoveEmployeeAction({ employeeId, name }: { employeeId: UUID; name: string }) {
  const navigate = useNavigate()
  const toast = useToast()
  const remove = useRemoveEmployee(employeeId)
  const [open, setOpen] = useState(false)
  const [serverError, setServerError] = useState<string | undefined>()

  return (
    <>
      <Button variant="danger-soft" onClick={() => setOpen(true)}>
        Remove from system
      </Button>
      <ReasonDialog
        open={open}
        onClose={() => setOpen(false)}
        title={`Remove ${name} from the system`}
        description="Their record leaves every list and their login is switched off. Payslips, documents and audit history are retained as required by law, and their email address is freed — so the same person can be onboarded again as a fresh employee if needed."
        label="Reason"
        confirmLabel="Remove employee"
        tone="danger"
        loading={remove.isPending}
        serverError={serverError}
        onSubmit={(reason) =>
          remove.mutate(reason, {
            onSuccess: () => {
              toast.success('Employee removed', `${name} no longer appears in the system.`)
              setOpen(false)
              navigate('/employees')
            },
            onError: (error) => {
              setServerError(error instanceof ApiError ? error.displayMessage : 'Could not remove')
              toast.fromError(error, 'Could not remove')
            },
          })
        }
      />
    </>
  )
}
