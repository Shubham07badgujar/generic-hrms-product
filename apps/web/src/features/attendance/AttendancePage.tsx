/**
 * Attendance — the computed day records.
 *
 * The server scopes the list: an employee sees their own days, a manager
 * their team's, HR everyone's. HR additionally corrects a day (marked as a
 * manual correction the sync never overwrites) and decides regularization
 * requests; employees raise those requests for their own days.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { PageHeader, Section } from '@/components/ui/Card'
import { Column, DataTable, CursorPager, FilterSelect, TableToolbar } from '@/components/ui/DataTable'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { TabPanel, Tabs } from '@/components/ui/Misc'
import { Modal } from '@/components/ui/Modal'
import { EmptyState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { useListParams } from '@/hooks/useListParams'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { formatDate, humanize } from '@/lib/format'
import {
  exportAttendance,
  useAcknowledgeRecord,
  useAttendanceRecords,
  useCorrectRecord,
  useDayPunches,
  useRegularizations,
  useRegularizationActions,
  type AttendanceRecordRow,
  type RegularizationRow,
} from '@/lib/attendanceQueries'
import { useEmployees } from '@/lib/queries'

const STATUS_TONES: Record<string, Tone> = {
  present: 'success',
  half_day: 'warning',
  absent: 'danger',
  on_leave: 'info',
  holiday: 'neutral',
  weekly_off: 'neutral',
  pending: 'warning',
  approved: 'success',
  rejected: 'danger',
}


function minutesLabel(minutes: number): string {
  if (!minutes) return '—'
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}

function timeLabel(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

function StatusCell({ row }: { row: AttendanceRecordRow }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge tone={STATUS_TONES[row.status] ?? 'neutral'}>{humanize(row.status)}</Badge>
      {row.is_late && row.late_counted && (
        <Badge tone="warning" title={`${row.late_minutes} min late — counts toward the monthly allowance`}>
          Late
        </Badge>
      )}
      {row.is_late && !row.late_counted && (
        <Badge tone="info" title={`${row.late_minutes} min late, made up by completing the full day — not counted`}>
          Late — compensated
        </Badge>
      )}
      {row.source !== 'device' && (
        <Badge tone="info" title="Protected from automatic recomputation">
          {humanize(row.source)}
        </Badge>
      )}
    </span>
  )
}

/** HR's override dialog, shared by the records list and the exceptions queue. */
function CorrectModal({
  row,
  onClose,
}: {
  row: AttendanceRecordRow
  onClose: () => void
}) {
  const toast = useToast()
  const correct = useCorrectRecord()
  const [status, setStatus] = useState(row.status)
  const [notes, setNotes] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      busy={correct.isPending}
      title={`Correct ${row.employee_name} — ${formatDate(row.date)}`}
      description="A manual correction is permanent: the device sync never overwrites it. The original and revised status, your reason and your name go to the audit trail."
      footer={
        <>
          <Button onClick={onClose} disabled={correct.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={correct.isPending}
            disabled={notes.trim().length < 5}
            onClick={() =>
              correct.mutate(
                { id: row.id, status, notes: notes.trim() },
                {
                  onSuccess: () => {
                    toast.success('Day corrected')
                    onClose()
                  },
                  onError: (error) => toast.fromError(error, 'Could not correct'),
                },
              )
            }
          >
            Save correction
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {row.notes && (
          <p className="rounded-lg bg-surface-muted px-3 py-2 text-xs text-ink-muted">
            System note: {row.notes}
          </p>
        )}
        <Select
          label="Status"
          value={status}
          onChange={(event) => setStatus(event.target.value as AttendanceRecordRow['status'])}
          options={['present', 'half_day', 'absent', 'on_leave'].map((s) => ({
            value: s, label: humanize(s),
          }))}
        />
        <TextArea
          label="Reason"
          required
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          description="Recorded on the day and in the audit trail."
        />
      </div>
    </Modal>
  )
}

/** One day, in full: verdict, timings and every punch behind it. */
function DayDetailModal({
  row,
  onClose,
}: {
  row: AttendanceRecordRow
  onClose: () => void
}) {
  const punches = useDayPunches(row.id)

  return (
    <Modal
      open
      onClose={onClose}
      title={`${row.employee_name} â€” ${formatDate(row.date)}`}
      description={`${row.employee_code} Â· every punch the devices recorded for this day.`}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusCell row={row} />
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-ink-subtle">First punch</dt>
            <dd>{timeLabel(row.first_in)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-subtle">Last punch</dt>
            <dd>{timeLabel(row.last_out)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-subtle">Worked</dt>
            <dd>{minutesLabel(row.worked_minutes)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-subtle">Late</dt>
            <dd>
              {row.is_late
                ? `${row.late_minutes} min${row.late_counted ? ' (counted)' : ' (compensated)'}`
                : 'No'}
            </dd>
          </div>
        </dl>
        {row.notes && (
          <p className="rounded-lg bg-surface-muted px-3 py-2 text-xs text-ink-muted">
            {row.notes}
          </p>
        )}
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-subtle">
            Punches
          </p>
          {punches.isLoading ? (
            <LoadingBlock label="Loading punches" />
          ) : (punches.data ?? []).length === 0 ? (
            <p className="text-sm text-ink-muted">No raw punches stored for this day.</p>
          ) : (
            <ul className="divide-y divide-line text-sm">
              {(punches.data ?? []).map((punch) => (
                <li key={punch.punched_at} className="flex justify-between py-1.5">
                  <span className="tabular-nums">{punch.time}</span>
                  <span className="text-xs text-ink-muted">{punch.device}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Modal>
  )
}

/** Pick an employee, a range and a format; the file downloads authenticated. */
function ExportDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const employees = useEmployees({ page_size: '200' })
  const [employee, setEmployee] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [fmt, setFmt] = useState<'xlsx' | 'csv'>('xlsx')
  const [busy, setBusy] = useState(false)

  return (
    <Modal
      open
      onClose={onClose}
      busy={busy}
      title="Export attendance"
      description="Date, first punch, last punch, worked hours and status â€” one row per day, as Excel or CSV. At most 92 days per file."
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!from || !to}
            onClick={() => {
              setBusy(true)
              exportAttendance({ employee: employee || undefined, date__gte: from, date__lte: to, fmt })
                .then(() => {
                  toast.success('Export downloaded')
                  onClose()
                })
                .catch((error: unknown) => toast.fromError(error, 'Could not export'))
                .finally(() => setBusy(false))
            }}
          >
            Download
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select
          label="Employee"
          value={employee}
          onChange={(event) => setEmployee(event.target.value)}
          placeholder="Everyone I can see"
          options={(employees.data?.data ?? []).map((person) => ({
            value: person.id,
            label: `${person.full_name} (${person.employee_code})`,
          }))}
        />
        <div className="grid grid-cols-2 gap-4">
          <TextInput label="From" required type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
          <TextInput label="To" required type="date" value={to} onChange={(event) => setTo(event.target.value)} />
        </div>
        <Select
          label="Format"
          value={fmt}
          onChange={(event) => setFmt(event.target.value as 'xlsx' | 'csv')}
          options={[
            { value: 'xlsx', label: 'Excel (.xlsx)' },
            { value: 'csv', label: 'CSV (.csv)' },
          ]}
        />
      </div>
    </Modal>
  )
}

export function AttendancePage() {
  const permissions = usePermissions()
  const [tab, setTab] = useState('records')

  const mayCorrect = permissions.can(RESOURCE.ATTENDANCE, ACTION.EDIT)
  const mayApprove = permissions.can(RESOURCE.REGULARIZATION, ACTION.APPROVE)

  return (
    <div className="space-y-5">
      <PageHeader
        title="Attendance"
        description="Daily records from the biometric devices, plus corrections."
      />
      <Section>
        <Tabs
          active={tab}
          onChange={setTab}
          items={[
            { id: 'records', label: 'Day records' },
            ...(mayCorrect ? [{ id: 'exceptions', label: 'Exceptions' }] : []),
            { id: 'regularizations', label: 'Regularizations' },
          ]}
        />
        <TabPanel id="records" active={tab}>
          <RecordsTab mayCorrect={mayCorrect} />
        </TabPanel>
        {mayCorrect && (
          <TabPanel id="exceptions" active={tab}>
            <ExceptionsTab />
          </TabPanel>
        )}
        <TabPanel id="regularizations" active={tab}>
          <RegularizationsTab mayApprove={mayApprove} />
        </TabPanel>
      </Section>
    </div>
  )
}

function RecordsTab({ mayCorrect }: { mayCorrect: boolean }) {
  const list = useListParams()

  // The default view is TODAY's roster — everyone's attendance for one day,
  // drill into a person for their month. Applied only on a clean entry, so
  // saved links and an explicit Reset still behave as the user asked.
  useEffect(() => {
    if (!list.hasFilters && !list.filters.date) {
      list.setParam('date', new Date().toISOString().slice(0, 10))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const query = useAttendanceRecords(list.queryParams)
  const employees = useEmployees({ page_size: '200' })
  const [correcting, setCorrecting] = useState<AttendanceRecordRow | null>(null)
  const [viewing, setViewing] = useState<AttendanceRecordRow | null>(null)
  const [exporting, setExporting] = useState(false)

  const columns: Column<AttendanceRecordRow>[] = [
    { key: 'date', header: 'Date', render: (row) => formatDate(row.date) },
    { key: 'employee', header: 'Employee', render: (row) => (
      <span>
        {row.employee_name}{' '}
        <span className="text-xs text-ink-subtle">{row.employee_code}</span>
      </span>
    ) },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <span title={row.notes || undefined}>
          <StatusCell row={row} />
        </span>
      ),
    },
    { key: 'first_in', header: 'In', render: (row) => timeLabel(row.first_in) },
    { key: 'last_out', header: 'Out', render: (row) => timeLabel(row.last_out) },
    { key: 'worked', header: 'Worked', render: (row) => minutesLabel(row.worked_minutes) },
    {
      key: 'actions',
      header: '',
      render: (row: AttendanceRecordRow) => (
        <span className="inline-flex gap-1.5">
          <Link to={`/attendance/employee/${row.employee}?month=${row.date.slice(0, 7)}`}>
            <Button size="sm" variant="ghost">Month</Button>
          </Link>
          <Button size="sm" variant="ghost" onClick={() => setViewing(row)}>
            Details
          </Button>
          {mayCorrect && (
            <Button size="sm" variant="ghost" onClick={() => setCorrecting(row)}>
              Correct
            </Button>
          )}
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-3">
      <TableToolbar
        search={list.searchDraft}
        onSearchChange={list.setSearchDraft}
        searchPlaceholder="Search…"
        onReset={list.reset}
        hasFilters={list.hasFilters}
      >
        <FilterSelect
          label="Employee"
          value={list.filters.employee ?? ''}
          onChange={(value) => list.setParam('employee', value)}
          options={[
            { value: '', label: 'All employees' },
            ...(employees.data?.data ?? []).map((person) => ({
              value: person.id,
              label: `${person.full_name} (${person.employee_code})`,
            })),
          ]}
        />
        <FilterSelect
          label="Status"
          value={list.filters.status ?? ''}
          onChange={(value) => list.setParam('status', value)}
          options={[
            { value: '', label: 'All statuses' },
            ...['present', 'half_day', 'absent', 'on_leave'].map((s) => ({
              value: s, label: humanize(s),
            })),
          ]}
        />
        <TextInput
          label=""
          title="Exactly this day"
          type="date"
          value={list.filters.date ?? ''}
          onChange={(event) => list.setParam('date', event.target.value)}
        />
        <TextInput
          label=""
          title="From date"
          type="date"
          value={list.filters.date__gte ?? ''}
          onChange={(event) => list.setParam('date__gte', event.target.value)}
        />
        <TextInput
          label=""
          title="To date"
          type="date"
          value={list.filters.date__lte ?? ''}
          onChange={(event) => list.setParam('date__lte', event.target.value)}
        />
        <Button size="sm" variant="secondary" onClick={() => setExporting(true)}>
          Export
        </Button>
      </TableToolbar>

      {query.isLoading ? (
        <LoadingBlock label="Loading attendance" />
      ) : (
        <DataTable
          columns={columns}
          rows={query.data?.data ?? []}
          rowKey={(row) => row.id}
          emptyState={
            <EmptyState
              compact
              title="No attendance records"
              description="Records appear once the eSSL sync brings punches in — or after a date-range import."
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

      {correcting && (
        <CorrectModal row={correcting} onClose={() => setCorrecting(null)} />
      )}
      {viewing && <DayDetailModal row={viewing} onClose={() => setViewing(null)} />}
      {exporting && <ExportDialog onClose={() => setExporting(false)} />}
    </div>
  )
}

const EXCEPTION_KINDS = [
  { id: '', label: 'All' },
  { id: 'half_day', label: 'Half days' },
  { id: 'late', label: 'Counted lates' },
  { id: 'absent', label: 'Absents' },
] as const

function ExceptionsTab() {
  const toast = useToast()
  const [kind, setKind] = useState('')
  const [cursor, setCursor] = useState('')
  const acknowledge = useAcknowledgeRecord()
  const [correcting, setCorrecting] = useState<AttendanceRecordRow | null>(null)

  const params: Record<string, string> = { exception: 'true' }
  if (kind === 'half_day' || kind === 'absent') params.status = kind
  if (kind === 'late') params.late_counted = 'true'
  if (cursor) params.cursor = cursor
  const query = useAttendanceRecords(params)

  const columns: Column<AttendanceRecordRow>[] = [
    { key: 'date', header: 'Date', render: (row) => formatDate(row.date) },
    { key: 'employee', header: 'Employee', render: (row) => (
      <span>
        {row.employee_name}{' '}
        <span className="text-xs text-ink-subtle">{row.employee_code}</span>
      </span>
    ) },
    { key: 'status', header: 'Status', render: (row) => <StatusCell row={row} /> },
    { key: 'worked', header: 'Worked', render: (row) => minutesLabel(row.worked_minutes) },
    {
      key: 'why',
      header: 'Why flagged',
      render: (row) => (
        <span className="text-xs text-ink-muted">{row.notes || humanize(row.status)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row) => (
        <span className="inline-flex gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            loading={acknowledge.isPending}
            onClick={() =>
              acknowledge.mutate(row.id, {
                onSuccess: () => toast.success('Acknowledged', 'The system status stands.'),
                onError: (error) => toast.fromError(error, 'Could not acknowledge'),
              })
            }
          >
            Acknowledge
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setCorrecting(row)}>
            Correct
          </Button>
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {EXCEPTION_KINDS.map((option) => (
          <Button
            key={option.id}
            size="sm"
            variant={kind === option.id ? 'primary' : 'ghost'}
            onClick={() => {
              setKind(option.id)
              setCursor('')
            }}
          >
            {option.label}
          </Button>
        ))}
      </div>
      {query.isLoading ? (
        <LoadingBlock label="Loading exceptions" />
      ) : (
        <DataTable
          columns={columns}
          rows={query.data?.data ?? []}
          rowKey={(row) => row.id}
          emptyState={
            <EmptyState
              compact
              title="Nothing awaiting review"
              description="Every system-flagged day has been acknowledged or corrected."
            />
          }
          footer={
            <CursorPager
              hasPrevious={Boolean(query.data?.meta.previous)}
              hasNext={Boolean(query.data?.meta.next)}
              onPrevious={() => setCursor(query.data?.meta.previous ?? '')}
              onNext={() => setCursor(query.data?.meta.next ?? '')}
              isFetching={query.isFetching}
            />
          }
        />
      )}
      {correcting && (
        <CorrectModal row={correcting} onClose={() => setCorrecting(null)} />
      )}
    </div>
  )
}

function RegularizationsTab({ mayApprove }: { mayApprove: boolean }) {
  const toast = useToast()
  const query = useRegularizations({})
  const actions = useRegularizationActions()
  const [raising, setRaising] = useState(false)
  const [date, setDate] = useState('')
  const [reason, setReason] = useState('')

  const rows = query.data?.data ?? []

  const columns: Column<RegularizationRow>[] = [
    { key: 'date', header: 'Date', render: (row) => formatDate(row.date) },
    { key: 'employee', header: 'Employee', render: (row) => row.employee_name },
    { key: 'reason', header: 'Reason', render: (row) => (
      <span className="line-clamp-2 max-w-[280px]" title={row.reason}>{row.reason}</span>
    ) },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <Badge tone={STATUS_TONES[row.status] ?? 'neutral'}>{humanize(row.status)}</Badge>,
    },
    ...(mayApprove
      ? [{
          key: 'actions',
          header: '',
          render: (row: RegularizationRow) =>
            row.status === 'pending' ? (
              <span className="flex gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() =>
                    actions.approve.mutate(
                      { id: row.id },
                      {
                        onSuccess: () => toast.success('Regularization approved'),
                        onError: (error) => toast.fromError(error, 'Could not approve'),
                      },
                    )
                  }
                >
                  Approve
                </Button>
                <Button
                  size="sm"
                  onClick={() =>
                    actions.reject.mutate(
                      { id: row.id },
                      {
                        onSuccess: () => toast.success('Regularization rejected'),
                        onError: (error) => toast.fromError(error, 'Could not reject'),
                      },
                    )
                  }
                >
                  Reject
                </Button>
              </span>
            ) : null,
        }]
      : []),
  ]

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="secondary" onClick={() => setRaising(true)}>
          Request a correction
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        emptyState={<EmptyState compact title="No regularization requests" />}
      />

      {raising && (
        <Modal
          open
          onClose={() => setRaising(false)}
          busy={actions.create.isPending}
          title="Request an attendance correction"
          description="Your manager or HR reviews and approves this."
          footer={
            <>
              <Button onClick={() => setRaising(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={actions.create.isPending}
                disabled={!date || reason.trim().length < 5}
                onClick={() =>
                  actions.create.mutate(
                    { date, reason: reason.trim() },
                    {
                      onSuccess: () => {
                        toast.success('Request submitted')
                        setRaising(false)
                        setDate('')
                        setReason('')
                      },
                      onError: (error) => toast.fromError(error, 'Could not submit'),
                    },
                  )
                }
              >
                Submit request
              </Button>
            </>
          }
        >
          <div className="space-y-4">
            <TextInput
              label="Date"
              type="date"
              required
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
            <TextArea
              label="What happened?"
              required
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. Device was not reading fingerprints; I signed the register instead."
            />
          </div>
        </Modal>
      )}
    </div>
  )
}
