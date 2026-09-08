/**
 * The audit viewer.
 *
 * Read-only by construction — there is no write endpoint to call. What each
 * person sees is decided entirely by the server's scope on `audit_log/view`:
 * a Department Head sees events about their own people, including ones HR or
 * Finance performed, while organisation-level events (role changes, statutory
 * rates) have no subject and stay visible only at ALL scope.
 *
 * `is_sensitive` is the server's classification, not a list this file keeps.
 */

import { useState } from 'react'
import { Card, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import {
  PagePager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Muted } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Modal'
import { TextInput } from '@/components/ui/Field'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useAuditLog, useAuditOptions, type AuditParams } from '@/lib/analyticsQueries'
import { formatDateTime, humanize } from '@/lib/format'
import type { AuditEntry } from '@/lib/types'

const ACTION_TONES: Record<string, Tone> = {
  create: 'success',
  update: 'neutral',
  delete: 'danger',
  approve: 'brand',
  reject: 'danger',
  override: 'danger',
  reverse: 'danger',
  recommend: 'info',
  allocate: 'info',
  return: 'info',
  login: 'neutral',
  login_failed: 'warning',
  access_pii: 'warning',
  export: 'warning',
  permission_change: 'danger',
  role_change: 'danger',
  credential_issue: 'warning',
  credential_view: 'warning',
}

/** Renders a before/after diff without pretending to understand the payload. */
function ValueDiff({ before, after }: { before: unknown; after: unknown }) {
  const beforeMap = (before ?? {}) as Record<string, unknown>
  const afterMap = (after ?? {}) as Record<string, unknown>
  const keys = Array.from(new Set([...Object.keys(beforeMap), ...Object.keys(afterMap)]))

  if (keys.length === 0) {
    return <Muted>No field-level detail was recorded for this event.</Muted>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-subtle">
            <th className="py-1.5 pr-3">Field</th>
            <th className="py-1.5 pr-3">Previous</th>
            <th className="py-1.5">New</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key} className="border-b border-line/60 last:border-0 align-top">
              <td className="py-1.5 pr-3 font-medium text-ink">{key}</td>
              <td className="py-1.5 pr-3 text-ink-muted">{renderValue(beforeMap[key])}</td>
              <td className="py-1.5 text-ink">{renderValue(afterMap[key])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function renderValue(value: unknown) {
  if (value === undefined) return <Muted>—</Muted>
  if (value === null) return <Muted>null</Muted>
  if (value === '***') {
    // The audit registry redacts sensitive fields at write time: it records
    // that a PAN changed, never what it was.
    return <Badge tone="neutral">redacted</Badge>
  }
  if (typeof value === 'object') {
    return <code className="text-xs">{JSON.stringify(value)}</code>
  }
  return <span className="break-words">{String(value)}</span>
}

function EntryDetail({ entry, onClose }: { entry: AuditEntry | null; onClose: () => void }) {
  if (!entry) return null

  return (
    <Drawer open={Boolean(entry)} onClose={onClose} title="Audit entry">
      <div className="space-y-4">
        <DescriptionList
          items={[
            { label: 'When', value: formatDateTime(entry.occurred_at) },
            { label: 'Who', value: `${entry.actor_name} (${entry.actor_email || 'system'})` },
            {
              label: 'What',
              value: (
                <span className="row row--tight">
                  <Badge tone={ACTION_TONES[entry.action] ?? 'neutral'}>{entry.action_label}</Badge>
                  {entry.is_sensitive ? <Badge tone="danger">Sensitive</Badge> : null}
                </span>
              ),
            },
            { label: 'Record', value: `${entry.entity_label || entry.entity_type}` },
            { label: 'Type', value: entry.entity_type },
            { label: 'Record ID', value: <code className="text-xs">{entry.entity_id}</code> },
            {
              label: 'About',
              value: entry.subject_name
                ? `${entry.subject_name} (${entry.subject_code}) · ${entry.subject_department ?? '—'}`
                : 'Not specific to one person',
            },
            { label: 'Resource', value: entry.resource ? humanize(entry.resource) : '—' },
            { label: 'Reason', value: entry.reason || '—' },
            { label: 'IP', value: entry.ip || '—' },
            {
              label: 'Request',
              value: entry.request_id ? <code className="text-xs">{entry.request_id}</code> : '—',
            },
          ]}
        />

        <Section title="What changed">
          <Card>
            <ValueDiff before={entry.before} after={entry.after} />
          </Card>
        </Section>
      </div>
    </Drawer>
  )
}

export function AuditPage() {
  const permissions = usePermissions()
  const canView = permissions.can(RESOURCE.AUDIT_LOG, ACTION.VIEW)

  const [filters, setFilters] = useState<AuditParams>({})
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<AuditEntry | null>(null)

  const params = { ...filters, ...(page > 1 ? { page: String(page) } : {}) }
  const query = useAuditLog(canView ? params : {})
  const options = useAuditOptions(canView)

  const columns: Column<AuditEntry>[] = [
    {
      key: 'when',
      header: 'When',
      render: (row) => (
        <span className="whitespace-nowrap text-sm">{formatDateTime(row.occurred_at)}</span>
      ),
    },
    {
      key: 'actor',
      header: 'Who',
      render: (row) => (
        <span className="text-sm">
          {row.actor_name}
          {row.actor_email && row.actor_email !== row.actor_name ? (
            <Muted> · {row.actor_email}</Muted>
          ) : null}
        </span>
      ),
    },
    {
      key: 'action',
      header: 'Action',
      render: (row) => (
        <span className="row row--tight">
          <Badge tone={ACTION_TONES[row.action] ?? 'neutral'}>{row.action_label}</Badge>
          {row.is_sensitive ? <Badge tone="danger">!</Badge> : null}
        </span>
      ),
    },
    {
      key: 'record',
      header: 'Record',
      render: (row) => (
        <span className="text-sm">
          {row.entity_label || row.entity_type}
          <Muted> · {row.entity_type.split('.').pop()}</Muted>
        </span>
      ),
    },
    {
      key: 'subject',
      header: 'About',
      secondary: true,
      render: (row) =>
        row.subject_name ? (
          <span className="text-sm">
            {row.subject_name}
            <Muted> · {row.subject_department ?? '—'}</Muted>
          </span>
        ) : (
          <Muted>—</Muted>
        ),
    },
    {
      key: 'open',
      header: 'Detail',
      headerSrOnly: true,
      render: (row) => (
        <Button size="sm" variant="ghost" onClick={() => setSelected(row)}>
          Detail
        </Button>
      ),
    },
  ]

  if (!canView) {
    return (
      <>
        <PageHeader title="Audit log" />
        <EmptyState
          title="You do not have access to the audit log"
          description="Audit visibility is restricted to administrators, executives and department leadership."
        />
      </>
    )
  }

  const rows = query.data?.data ?? []
  const sensitiveOnly = filters.sensitive_only === 'true'

  function setFilter(key: keyof AuditParams, value: string) {
    // A changed filter redefines the list, so the position in it resets.
    setPage(1)
    setFilters((current) => ({ ...current, [key]: value || undefined }))
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every recorded action within your visibility. Append-only — nothing here can be edited or deleted."
      />

      <Section title="Events">
        <TableToolbar>
          <FilterSelect
            label="Action"
            value={filters.action ?? ''}
            onChange={(value) => setFilter('action', value)}
            allLabel="All actions"
            options={(options.data?.actions ?? []).map((a) => ({
              value: a.value,
              label: a.sensitive ? `${a.label} (sensitive)` : a.label,
            }))}
          />
          <FilterSelect
            label="Resource"
            value={filters.resource ?? ''}
            onChange={(value) => setFilter('resource', value)}
            allLabel="All resources"
            options={(options.data?.resources ?? []).map((r) => ({
              value: r,
              label: humanize(r),
            }))}
          />
          <TextInput
            label=""
            type="date"
            aria-label="From date"
            value={filters.since ?? ''}
            onChange={(event) => setFilter('since', event.target.value)}
          />
          <TextInput
            label=""
            type="date"
            aria-label="To date"
            value={filters.until ?? ''}
            onChange={(event) => setFilter('until', event.target.value)}
          />
          <Button
            variant={sensitiveOnly ? 'primary' : 'secondary'}
            size="sm"
            onClick={() => setFilter('sensitive_only', sensitiveOnly ? '' : 'true')}
            aria-pressed={sensitiveOnly}
            title="Overrides, reversals, rejections, permission and credential events"
          >
            Sensitive only
          </Button>
        </TableToolbar>

        {query.isLoading ? (
          <TableSkeleton columns={columns.length} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No matching events"
            description="Nothing within your visibility matches these filters."
          />
        ) : (
          <>
            <DataTable rows={rows} columns={columns} rowKey={(row) => String(row.id)} />
            <PagePager
              page={query.data?.meta.page}
              pages={query.data?.meta.pages}
              count={query.data?.meta.count}
              pageSize={query.data?.meta.page_size}
              onPage={setPage}
              isFetching={query.isFetching}
            />
          </>
        )}
      </Section>

      <EntryDetail entry={selected} onClose={() => setSelected(null)} />
    </>
  )
}
