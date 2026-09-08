/**
 * The eSSL eTimeTrackLite integration — HR's control panel.
 *
 * Four tabs: connection status & devices, employee-ID mappings, punches with
 * no mapping yet (kept, never discarded), and the sync history. Everything
 * here sits behind the attendance_device resource, so this page simply does
 * not exist for employees. Credentials never reach this page — the status
 * endpoint returns the host and booleans only.
 */

import { useMemo, useState } from 'react'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { Column, DataTable } from '@/components/ui/DataTable'
import { Select, TextInput } from '@/components/ui/Field'
import { Banner, TabPanel, Tabs } from '@/components/ui/Misc'
import { Modal } from '@/components/ui/Modal'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useEmployees, useLocations } from '@/lib/queries'
import { formatDate, formatDateTime, humanize } from '@/lib/format'
import {
  useEsslActions,
  useEsslMappings,
  useEsslStatus,
  useShiftRules,
  useUpdateShiftRule,
  type ShiftRuleRow,
  useSyncRuns,
  useUnmapped,
  type EsslDevice,
  type EsslMapping,
  type EsslSyncRun,
  type UnmappedRow,
} from '@/lib/esslQueries'

const RUN_TONES: Record<string, Tone> = {
  running: 'info',
  completed: 'success',
  partial: 'warning',
  failed: 'danger',
}

export function EsslIntegrationPage() {
  const permissions = usePermissions()
  const [tab, setTab] = useState('status')
  const maySync = permissions.can(RESOURCE.ATTENDANCE_DEVICE, ACTION.IMPORT)
  const mayEdit = permissions.can(RESOURCE.ATTENDANCE_DEVICE, ACTION.EDIT)

  return (
    <div className="space-y-5">
      <PageHeader
        title="eSSL Integration"
        description="Biometric attendance from every branch's eTimeTrackLite devices."
      />
      <Section>
        <Tabs
          active={tab}
          onChange={setTab}
          items={[
            { id: 'status', label: 'Status & Devices' },
            { id: 'mappings', label: 'Employee mappings' },
            { id: 'unmapped', label: 'Unmapped punches' },
            { id: 'history', label: 'Sync history' },
          ]}
        />
        <TabPanel id="status" active={tab}>
          <StatusTab maySync={maySync} mayEdit={mayEdit} />
        </TabPanel>
        <TabPanel id="mappings" active={tab}>
          <MappingsTab mayEdit={mayEdit} />
        </TabPanel>
        <TabPanel id="unmapped" active={tab}>
          <UnmappedTab mayEdit={mayEdit} />
        </TabPanel>
        <TabPanel id="history" active={tab}>
          <HistoryTab />
        </TabPanel>
      </Section>
    </div>
  )
}

/* =============================================================== status */

function StatusTab({ maySync, mayEdit }: { maySync: boolean; mayEdit: boolean }) {
  const toast = useToast()
  const status = useEsslStatus()
  const rules = useShiftRules()
  const [editingRule, setEditingRule] = useState<ShiftRuleRow | null>(null)
  const actions = useEsslActions()
  const [rangeOpen, setRangeOpen] = useState(false)
  const [deviceOpen, setDeviceOpen] = useState(false)

  if (status.isLoading) return <LoadingBlock label="Checking the connection" />
  const data = status.data
  if (!data) return <EmptyState title="Status unavailable" />

  return (
    <div className="space-y-4">
      {!data.enabled ? (
        <Banner tone="warning" title="The integration is switched off on the server">
          Set ESSL_INTEGRATION_ENABLED and the server credentials in the environment, then
          run <code className="mx-1">manage.py essl_check</code>. Devices and mappings can be
          prepared meanwhile — nothing syncs until it is enabled.
        </Banner>
      ) : (
        <Banner tone="success" title={`Connected to ${data.base_host}`}>
          {data.affects_payroll
            ? 'Attendance is LIVE for payroll: absences and half days reduce paid days.'
            : 'Report-only mode: attendance is computed and shown, payroll is unaffected until ATTENDANCE_AFFECTS_PAYROLL is enabled.'}
        </Banner>
      )}

      {data.last_run && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Last sync"
            value={formatDateTime(data.last_run.started_at)}
            hint={humanize(data.last_run.kind)}
            tone={data.last_run.status === 'completed' ? 'success' : 'warning'}
          />
          <StatCard label="Punches fetched" value={String(data.last_run.punches_fetched)} />
          <StatCard label="New punches" value={String(data.last_run.punches_created)} />
          <StatCard
            label="Unmapped"
            value={String(data.last_run.punches_unmapped)}
            tone={data.last_run.punches_unmapped ? 'warning' : undefined}
          />
        </div>
      )}

      <Card className="space-y-3">
        <CardHeader
          title="Devices"
          description="One row per biometric device, addressed by its serial number."
          action={
            <span className="flex gap-2">
              {mayEdit && (
                <Button size="sm" variant="secondary" onClick={() => setDeviceOpen(true)}>
                  Add device
                </Button>
              )}
              {maySync && (
                <>
                  <Button size="sm" onClick={() => setRangeOpen(true)}>
                    Date-range sync
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    loading={actions.syncNow.isPending}
                    onClick={() =>
                      actions.syncNow.mutate(undefined, {
                        onSuccess: (run) =>
                          toast.success(
                            'Sync finished',
                            `${run.punches_created} new punches (${run.punches_duplicate} already known).`,
                          ),
                        onError: (error) => toast.fromError(error, 'Sync failed'),
                      })
                    }
                  >
                    Sync now
                  </Button>
                </>
              )}
            </span>
          }
        />
        <DeviceTable devices={data.devices} mayEdit={mayEdit} />
      </Card>

      <Card className="space-y-3">
        <CardHeader
          title="Shift rules"
          description="Late is after start + grace. A late that still completes the full day's hours is compensated and never counted; counted lates beyond the monthly allowance become half days. Every edit here is audited."
        />
        <DataTable
          columns={[
            { key: 'where', header: 'Location', render: (r: ShiftRuleRow) => r.location_name ?? 'Default (all others)' },
            { key: 'hours', header: 'Hours', render: (r: ShiftRuleRow) => `${r.start_time.slice(0, 5)} – ${r.end_time.slice(0, 5)}` },
            { key: 'grace', header: 'Grace', render: (r: ShiftRuleRow) => `${r.grace_minutes} min` },
            { key: 'lates', header: 'Lates allowed / month', render: (r: ShiftRuleRow) => String(r.allowed_late_per_month) },
            { key: 'full', header: 'Full day', render: (r: ShiftRuleRow) => `${r.full_day_hours}h` },
            { key: 'half', header: 'Half day below', render: (r: ShiftRuleRow) => `${r.half_day_below_hours}h` },
            ...(mayEdit
              ? [{
                  key: 'actions',
                  header: '',
                  render: (r: ShiftRuleRow) => (
                    <Button size="sm" variant="ghost" onClick={() => setEditingRule(r)}>
                      Edit
                    </Button>
                  ),
                }]
              : []),
          ]}
          rows={rules.data ?? []}
          rowKey={(r) => r.id}
          emptyState={<EmptyState compact title="No shift rules seeded yet" />}
        />
      </Card>

      {rangeOpen && <RangeSyncDialog onClose={() => setRangeOpen(false)} />}
      {deviceOpen && <DeviceDialog onClose={() => setDeviceOpen(false)} />}
      {editingRule && (
        <ShiftRuleDialog rule={editingRule} onClose={() => setEditingRule(null)} />
      )}
    </div>
  )
}

function DeviceTable({ devices, mayEdit }: { devices: EsslDevice[]; mayEdit: boolean }) {
  const toast = useToast()
  const actions = useEsslActions()
  const [removing, setRemoving] = useState<EsslDevice | null>(null)

  const columns: Column<EsslDevice>[] = [
    { key: 'name', header: 'Device', render: (row) => row.name },
    { key: 'serial', header: 'Serial number', render: (row) => <code>{row.serial_number}</code> },
    { key: 'location', header: 'Branch', render: (row) => row.location_name ?? '—' },
    {
      key: 'state',
      header: 'Last sync',
      render: (row) => (
        <span className="inline-flex items-center gap-1.5">
          <Badge
            tone={row.last_sync_status === 'ok' ? 'success' : row.last_sync_status === 'failed' ? 'danger' : 'neutral'}
            title={row.last_sync_error || undefined}
          >
            {humanize(row.last_sync_status)}
          </Badge>
          {row.last_synced_at && (
            <span className="text-xs text-ink-subtle">{formatDateTime(row.last_synced_at)}</span>
          )}
          {!row.is_enabled && <Badge tone="neutral">Disabled</Badge>}
        </span>
      ),
    },
    ...(mayEdit
      ? [{
          key: 'actions',
          header: '',
          render: (row: EsslDevice) => (
            <span className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  actions.updateDevice.mutate(
                    { id: row.id, is_enabled: !row.is_enabled },
                    {
                      onSuccess: () => toast.success(row.is_enabled ? 'Device disabled' : 'Device enabled'),
                      onError: (error) => toast.fromError(error, 'Could not update'),
                    },
                  )
                }
              >
                {row.is_enabled ? 'Disable' : 'Enable'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRemoving(row)}>
                Remove
              </Button>
            </span>
          ),
        }]
      : []),
  ]

  return (
    <>
      <DataTable
        columns={columns}
        rows={devices}
        rowKey={(row) => row.id}
        emptyState={
          <EmptyState
            compact
            title="No devices registered"
            description="Add each branch's device with its serial number from eTimeTrackLite."
          />
        }
      />
      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        tone="danger"
        title="Remove this device?"
        description={`${removing?.name ?? ''} — its already-imported punches stay; only future syncs stop.`}
        confirmLabel="Remove device"
        loading={actions.removeDevice.isPending}
        onConfirm={() =>
          removing &&
          actions.removeDevice.mutate(removing.id, {
            onSuccess: () => {
              toast.success('Device removed')
              setRemoving(null)
            },
            onError: (error) => toast.fromError(error, 'Could not remove'),
          })
        }
      />
    </>
  )
}

function DeviceDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const actions = useEsslActions()
  const locations = useLocations()
  const [name, setName] = useState('')
  const [serial, setSerial] = useState('')
  const [location, setLocation] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      busy={actions.createDevice.isPending}
      title="Add a biometric device"
      description="The serial number is how eTimeTrackLite identifies the device — find it under Devices in the eSSL web admin."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={actions.createDevice.isPending}
            disabled={!name.trim() || !serial.trim()}
            onClick={() =>
              actions.createDevice.mutate(
                {
                  name: name.trim(),
                  serial_number: serial.trim(),
                  location: location || null,
                },
                {
                  onSuccess: () => {
                    toast.success('Device added')
                    onClose()
                  },
                  onError: (error) => toast.fromError(error, 'Could not add the device'),
                },
              )
            }
          >
            Add device
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextInput
          label="Name"
          required
          placeholder="e.g. Main Branch — entrance"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <TextInput
          label="Serial number"
          required
          placeholder="e.g. CGXH201360307"
          value={serial}
          onChange={(event) => setSerial(event.target.value)}
        />
        <Select
          label="Branch"
          value={location}
          onChange={(event) => setLocation(event.target.value)}
          placeholder="Select the branch this device sits at"
          options={(locations.data ?? []).map((row) => ({ value: row.id, label: row.name }))}
        />
      </div>
    </Modal>
  )
}

function RangeSyncDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const actions = useEsslActions()
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      busy={actions.syncRange.isPending}
      title="Import a date range"
      description="Re-reads every device for the period. Already-imported punches are recognised and skipped, so overlapping is safe."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={actions.syncRange.isPending}
            disabled={!dateFrom || !dateTo}
            onClick={() =>
              actions.syncRange.mutate(
                { date_from: dateFrom, date_to: dateTo },
                {
                  onSuccess: (run) => {
                    toast.success(
                      'Historical import finished',
                      `${run.punches_created} new punches across ${run.devices_total} device(s).`,
                    )
                    onClose()
                  },
                  onError: (error) => toast.fromError(error, 'Import failed'),
                },
              )
            }
          >
            Run import
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="From" type="date" required value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        <TextInput label="To" type="date" required value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
      </div>
    </Modal>
  )
}

/* ============================================================= mappings */

function MappingsTab({ mayEdit }: { mayEdit: boolean }) {
  const toast = useToast()
  const mappings = useEsslMappings()
  const actions = useEsslActions()
  const [adding, setAdding] = useState<{ esslId?: string; employee?: string } | null>(null)

  // One employee may hold several IDs — one per site. Group so that shows.
  const grouped = useMemo(() => {
    const byEmployee = new Map<string, EsslMapping[]>()
    for (const row of mappings.data ?? []) {
      const list = byEmployee.get(row.employee) ?? []
      list.push(row)
      byEmployee.set(row.employee, list)
    }
    return [...byEmployee.values()].sort((a, b) =>
      a[0].employee_name.localeCompare(b[0].employee_name),
    )
  }, [mappings.data])

  const multiSite = grouped.filter((rows) => rows.length > 1).length

  return (
    <div className="space-y-3">
      <Banner tone="info" title="One employee, one ID per site">
        Someone who works at more than one branch is enrolled separately on each site's device
        and carries a different eSSL ID at each — map them all to the same employee and every
        punch lands on that person. The reverse is never allowed: an ID belongs to exactly one
        employee. Mapping an ID also claims all of its earlier unmapped punches.
      </Banner>
      {mayEdit && (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-ink-subtle">
            {grouped.length} employee{grouped.length === 1 ? '' : 's'} mapped
            {multiSite > 0 && ` · ${multiSite} working across more than one site`}
          </p>
          <Button variant="secondary" onClick={() => setAdding({})}>
            Add mapping
          </Button>
        </div>
      )}

      {grouped.length === 0 ? (
        <EmptyState compact title="No mappings yet" />
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          {grouped.map((rows, index) => (
            <div
              key={rows[0].employee}
              className={index > 0 ? 'border-t border-line' : undefined}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
                <p className="text-sm font-medium">
                  {rows[0].employee_name}{' '}
                  <span className="text-ink-subtle">({rows[0].employee_code})</span>
                  {rows.length > 1 && (
                    <span className="ml-2 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] text-brand">
                      {rows.length} sites
                    </span>
                  )}
                </p>
                {mayEdit && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setAdding({ employee: rows[0].employee })}
                  >
                    Add another ID
                  </Button>
                )}
              </div>
              <ul className="divide-y divide-line px-4 pb-3">
                {rows.map((row) => (
                  <li key={row.id} className="flex items-center justify-between gap-3 py-2">
                    <span className="flex items-center gap-3 text-sm">
                      <code className="rounded bg-canvas px-1.5 py-0.5">{row.essl_user_id}</code>
                      <span className="text-ink-subtle">
                        {row.location_name ?? 'No site recorded'}
                      </span>
                    </span>
                    {mayEdit && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          actions.removeMapping.mutate(row.id, {
                            onSuccess: () =>
                              toast.success(
                                'Mapping removed',
                                rows.length > 1
                                  ? "This employee's other IDs are unchanged."
                                  : undefined,
                              ),
                            onError: (error) => toast.fromError(error, 'Could not remove'),
                          })
                        }
                      >
                        Remove
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {adding && (
        <MappingDialog
          initialEsslId={adding.esslId ?? ''}
          initialEmployee={adding.employee ?? ''}
          onClose={() => setAdding(null)}
        />
      )}
    </div>
  )
}

function MappingDialog({
  onClose,
  initialEsslId = '',
  initialEmployee = '',
}: {
  onClose: () => void
  initialEsslId?: string
  initialEmployee?: string
}) {
  const toast = useToast()
  const actions = useEsslActions()
  const [esslId, setEsslId] = useState(initialEsslId)
  const [search, setSearch] = useState('')
  const [employee, setEmployee] = useState(initialEmployee)
  const [location, setLocation] = useState('')
  const employees = useEmployees({ search, page_size: '20' })
  const locations = useLocations()
  // When adding a second ID for someone, they are already chosen — make sure
  // the picker can show them even before anyone types a search.
  const options = useMemo(() => {
    const rows = employees.data?.data ?? []
    return rows.map((row) => ({
      value: row.id,
      label: `${row.full_name} — ${row.employee_code}`,
    }))
  }, [employees.data])

  return (
    <Modal
      open
      onClose={onClose}
      busy={actions.createMapping.isPending}
      title="Map an eSSL user ID to an employee"
      description="All of this ID's punches — past and future — attach to the chosen employee. An employee may hold one ID per site they work at."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={actions.createMapping.isPending}
            disabled={!esslId.trim() || !employee}
            onClick={() =>
              actions.createMapping.mutate(
                { essl_user_id: esslId.trim(), employee, location: location || null },
                {
                  onSuccess: () => {
                    toast.success('Mapping created', 'Existing unmapped punches were attached.')
                    onClose()
                  },
                  onError: (error) => toast.fromError(error, 'Could not create the mapping'),
                },
              )
            }
          >
            Create mapping
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextInput
          label="eSSL user ID"
          required
          value={esslId}
          onChange={(event) => setEsslId(event.target.value)}
          description="The user ID on the device / in eTimeTrackLite."
        />
        <TextInput
          label="Find employee"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search by name or code…"
        />
        <Select
          label="Employee"
          required
          value={employee}
          onChange={(event) => setEmployee(event.target.value)}
          placeholder="Select the employee"
          options={options}
        />
        <Select
          label="Location / branch"
          value={location}
          onChange={(event) => setLocation(event.target.value)}
          placeholder="Optional — where this ID is enrolled"
          description="Which site's device knows the employee by this ID."
          options={(locations.data ?? []).map((row) => ({ value: row.id, label: row.name }))}
        />
      </div>
    </Modal>
  )
}

/* ============================================================= unmapped */

function UnmappedTab({ mayEdit }: { mayEdit: boolean }) {
  const unmapped = useUnmapped()
  const [mapping, setMapping] = useState<string | null>(null)

  const columns: Column<UnmappedRow>[] = [
    { key: 'id', header: 'eSSL user ID', render: (row) => <code>{row.essl_user_id}</code> },
    { key: 'punches', header: 'Punches held', render: (row) => String(row.punches) },
    { key: 'first', header: 'First seen', render: (row) => formatDate(row.first_seen) },
    { key: 'last', header: 'Last seen', render: (row) => formatDateTime(row.last_seen) },
    ...(mayEdit
      ? [{
          key: 'actions',
          header: '',
          render: (row: UnmappedRow) => (
            <Button size="sm" variant="primary" onClick={() => setMapping(row.essl_user_id)}>
              Map to employee
            </Button>
          ),
        }]
      : []),
  ]

  return (
    <div className="space-y-3">
      <Banner tone="info" title="Nothing is discarded">
        Punches from IDs with no mapping are held here. The moment you map an ID, its whole
        history attaches to the employee and their attendance is computed.
      </Banner>
      <DataTable
        columns={columns}
        rows={unmapped.data ?? []}
        rowKey={(row) => row.essl_user_id}
        emptyState={<EmptyState compact title="Every punch is mapped" />}
      />
      {mapping !== null && (
        <MappingDialog initialEsslId={mapping} onClose={() => setMapping(null)} />
      )}
    </div>
  )
}

/* ============================================================== history */

function HistoryTab() {
  const runs = useSyncRuns()

  const columns: Column<EsslSyncRun>[] = [
    { key: 'when', header: 'Started', render: (row) => formatDateTime(row.started_at) },
    { key: 'kind', header: 'Kind', render: (row) => humanize(row.kind) },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <Badge tone={RUN_TONES[row.status] ?? 'neutral'}>{humanize(row.status)}</Badge>,
    },
    {
      key: 'counts',
      header: 'Punches (new / dup / unmapped)',
      render: (row) => `${row.punches_created} / ${row.punches_duplicate} / ${row.punches_unmapped}`,
    },
    {
      key: 'errors',
      header: 'Errors',
      render: (row) =>
        row.errors.length ? (
          <span className="line-clamp-2 max-w-[320px] text-xs text-danger" title={row.errors.map((e) => `${e.device}: ${e.error}`).join('\n')}>
            {row.errors.map((e) => e.device).join(', ')}
          </span>
        ) : ('—'),
    },
  ]

  return (
    <DataTable
      columns={columns}
      rows={runs.data ?? []}
      rowKey={(row) => row.id}
      emptyState={<EmptyState compact title="No syncs have run yet" />}
    />
  )
}


/** Edit one branch's attendance policy. The server validates and audits. */
function ShiftRuleDialog({
  rule,
  onClose,
}: {
  rule: ShiftRuleRow
  onClose: () => void
}) {
  const toast = useToast()
  const update = useUpdateShiftRule()
  const [values, setValues] = useState({
    start_time: rule.start_time.slice(0, 5),
    end_time: rule.end_time.slice(0, 5),
    grace_minutes: String(rule.grace_minutes),
    allowed_late_per_month: String(rule.allowed_late_per_month),
    half_day_below_hours: String(rule.half_day_below_hours),
    full_day_hours: String(rule.full_day_hours),
  })

  const set = (key: keyof typeof values) =>
    (event: React.ChangeEvent<HTMLInputElement>) =>
      setValues((current) => ({ ...current, [key]: event.target.value }))

  return (
    <Modal
      open
      onClose={onClose}
      busy={update.isPending}
      title={`Shift rule — ${rule.location_name ?? 'Default'}`}
      description="Applies to every employee at this branch from the next recompute onward. The change is written to the audit trail."
      footer={
        <>
          <Button onClick={onClose} disabled={update.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={update.isPending}
            onClick={() =>
              update.mutate(
                {
                  id: rule.id,
                  start_time: values.start_time,
                  end_time: values.end_time,
                  grace_minutes: Number(values.grace_minutes),
                  allowed_late_per_month: Number(values.allowed_late_per_month),
                  half_day_below_hours: values.half_day_below_hours,
                  full_day_hours: values.full_day_hours,
                },
                {
                  onSuccess: () => {
                    toast.success('Shift rule updated')
                    onClose()
                  },
                  onError: (error) => toast.fromError(error, 'Could not update'),
                },
              )
            }
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-4">
        <TextInput label="Start" type="time" value={values.start_time} onChange={set('start_time')} />
        <TextInput label="End" type="time" value={values.end_time} onChange={set('end_time')} />
        <TextInput label="Grace (minutes)" type="number" value={values.grace_minutes} onChange={set('grace_minutes')} />
        <TextInput label="Lates allowed / month" type="number" value={values.allowed_late_per_month} onChange={set('allowed_late_per_month')} />
        <TextInput
          label="Full day (hours)"
          type="number"
          step="0.5"
          value={values.full_day_hours}
          onChange={set('full_day_hours')}
          description="A late arrival that still works this many hours is compensated."
        />
        <TextInput
          label="Half day below (hours)"
          type="number"
          step="0.5"
          value={values.half_day_below_hours}
          onChange={set('half_day_below_hours')}
        />
      </div>
    </Modal>
  )
}
