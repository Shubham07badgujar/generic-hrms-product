/**
 * The asset register.
 *
 * HR Head creates and edits assets here; assignment happens from here or from
 * an employee's profile — both call the same endpoints, because the backend
 * has one `allocate()` entry point and a second UI path would be a second
 * place to get the rules wrong.
 *
 * Status is never edited directly: a new asset is born Available, assignment
 * makes it Assigned, a return makes it Available again. The register only
 * reflects what the allocations say.
 */

import { useState } from 'react'
import { PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useAllocateAsset,
  useAssetAllocations,
  useAssets,
  useCreateAsset,
  useUpdateAsset,
  type AssetPayload,
} from '@/lib/lifecycleQueries'
import { useCurrentEmployees, useLocations } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { ApiError } from '@/lib/api'
import { formatDate, humanize } from '@/lib/format'
import type { Asset, AssetStatusValue } from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const STATUS_TONES: Record<AssetStatusValue, Tone> = {
  available: 'success',
  allocated: 'info',
  in_maintenance: 'warning',
  retired: 'neutral',
  lost: 'danger',
}

/** HR's word is "Assigned"; the API's is "allocated". The UI speaks HR's. */
export function assetStatusLabel(status: AssetStatusValue): string {
  return status === 'allocated' ? 'Assigned' : humanize(status)
}

const EMPTY_FORM = {
  asset_tag: '',
  name: '',
  serial_number: '',
  location: '',
  purchase_date: '',
  notes: '',
}

export function AssetsPage() {
  const permissions = usePermissions()
  const toast = useToast()
  const list = useListParams({ ordering: 'asset_tag' })
  const query = useAssets(list.queryParams)
  const employees = useCurrentEmployees()
  const locations = useLocations()
  const allocate = useAllocateAsset()
  const create = useCreateAsset()
  const update = useUpdateAsset()

  const [allocating, setAllocating] = useState<Asset | null>(null)
  const [employee, setEmployee] = useState('')
  const [error, setError] = useState<string | undefined>()

  // One modal serves create and edit; `editing` decides which.
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Asset | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})

  const [historyOf, setHistoryOf] = useState<Asset | null>(null)

  const rows = query.data?.data ?? []
  const mayCreate = permissions.can(RESOURCE.ASSET, ACTION.CREATE)
  const mayEdit = permissions.can(RESOURCE.ASSET, ACTION.EDIT)
  const mayAllocate = permissions.can(RESOURCE.ASSET_ALLOCATION, ACTION.CREATE)

  const saving = editing ? update : create

  function openCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormErrors({})
    setFormOpen(true)
  }

  function openEdit(asset: Asset) {
    setEditing(asset)
    setForm({
      asset_tag: asset.asset_tag,
      name: asset.name,
      serial_number: asset.serial_number,
      location: asset.location ?? '',
      purchase_date: asset.purchase_date ?? '',
      notes: asset.notes,
    })
    setFormErrors({})
    setFormOpen(true)
  }

  function submitForm() {
    setFormErrors({})
    const payload: AssetPayload = {
      asset_tag: form.asset_tag.trim(),
      name: form.name.trim(),
      serial_number: form.serial_number.trim(),
      location: form.location || null,
      purchase_date: form.purchase_date || null,
      notes: form.notes,
    }
    const done = {
      onSuccess: () => {
        toast.success(editing ? 'Asset updated' : 'Asset added', payload.asset_tag)
        setFormOpen(false)
      },
      onError: (submitError: Error) => {
        if (submitError instanceof ApiError) setFormErrors(submitError.fieldErrors)
        toast.fromError(submitError, editing ? 'Could not update' : 'Could not add the asset')
      },
    }
    if (editing) update.mutate({ id: editing.id, ...payload }, done)
    else create.mutate(payload, done)
  }

  const columns: Array<Column<Asset>> = [
    {
      key: 'tag',
      header: 'Asset',
      sortKey: 'asset_tag',
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: LIME }}
          />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.asset_tag}</p>
            <p className="truncate text-xs text-ink-muted">{row.name}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'serial',
      header: 'Serial',
      secondary: true,
      render: (row) => <span className="font-mono text-xs">{row.serial_number || '—'}</span>,
    },
    {
      key: 'location',
      header: 'Location',
      secondary: true,
      render: (row) => row.location_name ?? '—',
    },
    {
      key: 'purchased',
      header: 'Purchased',
      secondary: true,
      sortKey: 'purchase_date',
      render: (row) => formatDate(row.purchase_date),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <Badge tone={STATUS_TONES[row.status] ?? 'neutral'}>{assetStatusLabel(row.status)}</Badge>
      ),
    },
    {
      key: 'holder',
      header: 'Held by',
      render: (row) => row.held_by_name ?? <span className="text-ink-subtle">—</span>,
    },
    {
      key: 'actions',
      header: 'Actions',
      headerSrOnly: true,
      align: 'right',
      render: (row) => (
        <div className="flex justify-end gap-2">
          <Button size="sm" onClick={() => setHistoryOf(row)}>
            History
          </Button>
          {mayEdit && (
            <Button size="sm" onClick={() => openEdit(row)}>
              Edit
            </Button>
          )}
          {mayAllocate && row.status === 'available' && (
            <Button size="sm" variant="primary" onClick={() => setAllocating(row)}>
              Assign
            </Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span
                aria-hidden
                className="block h-4 w-4 rounded-full border-[3px]"
                style={{ borderColor: LIME }}
              />
            </span>
            Assets
          </span>
        }
        description="Company property and who currently holds it."
        actions={
          mayCreate ? (
            <Button variant="primary" onClick={openCreate}>
              Add asset
            </Button>
          ) : undefined
        }
      />

      <Section>
        <div className="space-y-4">
          <TableToolbar
            search={list.searchDraft}
            onSearchChange={list.setSearchDraft}
            searchPlaceholder="Search tag, name or serial…"
            onReset={list.reset}
            hasFilters={list.hasFilters}
          >
            <FilterSelect
              label="Status"
              value={list.filters.status ?? ''}
              onChange={(value) => list.setParam('status', value)}
              options={[
                { value: 'available', label: 'Available' },
                { value: 'allocated', label: 'Assigned' },
                { value: 'in_maintenance', label: 'In maintenance' },
                { value: 'retired', label: 'Retired' },
                { value: 'lost', label: 'Lost' },
              ]}
              allLabel="All statuses"
            />
          </TableToolbar>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Assets"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={7} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No assets match' : 'No assets registered'}
                  description={
                    mayCreate
                      ? 'Add company property here — laptops, phones, SIM cards, access cards, keys — and assign it from an employee’s profile or this register.'
                      : 'Assets are added to the register before they can be assigned.'
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

      {/* ------------------------------------------------ add / edit asset */}
      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        busy={saving.isPending}
        size="md"
        title={editing ? `Edit ${editing.asset_tag}` : 'Add asset'}
        description={
          editing
            ? 'The register entry, not the assignment — who holds it is managed with Assign and Return.'
            : 'New assets start as Available and can be assigned right away.'
        }
        footer={
          <>
            <Button onClick={() => setFormOpen(false)} disabled={saving.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={saving.isPending}
              disabled={!form.asset_tag.trim() || !form.name.trim()}
              onClick={submitForm}
            >
              {editing ? 'Save changes' : 'Add asset'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput
              label="Asset name"
              required
              placeholder="e.g. Dell Laptop, SIM Card, Access Card"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              error={formErrors.name}
            />
            <TextInput
              label="Asset ID"
              required
              placeholder="e.g. AST-001"
              value={form.asset_tag}
              onChange={(event) => setForm({ ...form, asset_tag: event.target.value })}
              error={formErrors.asset_tag}
              description="Unique across the register."
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput
              label="Serial / identification number"
              value={form.serial_number}
              onChange={(event) => setForm({ ...form, serial_number: event.target.value })}
              error={formErrors.serial_number}
            />
            <TextInput
              label="Purchase date"
              type="date"
              value={form.purchase_date}
              onChange={(event) => setForm({ ...form, purchase_date: event.target.value })}
              error={formErrors.purchase_date}
            />
          </div>
          <Select
            label="Location"
            placeholder="Select a location"
            value={form.location}
            onChange={(event) => setForm({ ...form, location: event.target.value })}
            options={(locations.data ?? []).map((item) => ({
              value: item.id,
              label: item.name,
            }))}
            error={formErrors.location}
          />
          <TextArea
            label="Remarks"
            rows={3}
            value={form.notes}
            onChange={(event) => setForm({ ...form, notes: event.target.value })}
            error={formErrors.notes}
          />
        </div>
      </Modal>

      {/* ------------------------------------------------------- assign one */}
      <Modal
        open={allocating !== null}
        onClose={() => setAllocating(null)}
        busy={allocate.isPending}
        size="sm"
        title="Assign asset"
        description={allocating ? `${allocating.asset_tag} · ${allocating.name}` : ''}
        footer={
          <>
            <Button onClick={() => setAllocating(null)}>Cancel</Button>
            <Button
              variant="primary"
              loading={allocate.isPending}
              disabled={!employee}
              onClick={() => {
                setError(undefined)
                allocate.mutate(
                  { asset: allocating!.id, employee },
                  {
                    onSuccess: () => {
                      toast.success('Asset assigned')
                      setAllocating(null)
                      setEmployee('')
                      void query.refetch()
                    },
                    onError: (submitError) => {
                      if (submitError instanceof ApiError) setError(submitError.displayMessage)
                      toast.fromError(submitError, 'Could not assign')
                    },
                  },
                )
              }}
            >
              Assign
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          <Select
            label="Assign to"
            required
            placeholder="Select an employee"
            value={employee}
            onChange={(event) => setEmployee(event.target.value)}
            options={employees.rows.map((item) => ({
              value: item.id,
              label: `${item.full_name} — ${item.department_name ?? ''}`,
            }))}
          />
        </div>
      </Modal>

      <AssetHistoryModal asset={historyOf} onClose={() => setHistoryOf(null)} />
    </>
  )
}

/** Who has held this asset, newest first. Closed assignments are kept forever. */
function AssetHistoryModal({ asset, onClose }: { asset: Asset | null; onClose: () => void }) {
  const history = useAssetAllocations(asset ? { asset: asset.id } : {})
  const rows = asset ? (history.data?.data ?? []) : []

  return (
    <Modal
      open={asset !== null}
      onClose={onClose}
      size="md"
      title="Assignment history"
      description={asset ? `${asset.asset_tag} · ${asset.name}` : ''}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {history.isLoading ? (
        <TableSkeleton columns={3} />
      ) : rows.length === 0 ? (
        <EmptyState compact title="Never assigned" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-3 py-3">
              <span
                aria-hidden
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: LIME }}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{row.employee_name}</p>
                <p className="text-xs text-ink-muted">
                  Assigned {formatDate(row.allocated_at)}
                  {row.returned_at ? ` · returned ${formatDate(row.returned_at)}` : ''}
                </p>
              </div>
              <Badge
                tone={
                  row.status === 'active'
                    ? 'info'
                    : row.status === 'written_off'
                      ? 'danger'
                      : 'neutral'
                }
              >
                {row.status === 'active' ? 'Currently held' : humanize(row.status)}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}

/** Kept for a future asset detail route; the register links here today. */
export function formatAllocationDate(value: string | null) {
  return formatDate(value)
}
