/**
 * The applications list.
 *
 * Scoping is the server's job. A Medical Director gets medical applications
 * only, an interviewer gets the ones they are booked on, and HR gets
 * everything — all from the same request to the same endpoint. This page adds
 * no department filter of its own, because doing so would imply the narrowing
 * happens here.
 */

import { Link, useNavigate } from 'react-router-dom'
import { PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { ApplicationStatusBadge, Badge } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { useApplications } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { RESOURCE } from '@/lib/permissions'
import { formatDate } from '@/lib/format'
import type { Application } from '@/lib/types'

const STATUS_OPTIONS = [
  { value: 'active', label: 'In progress' },
  { value: 'selected', label: 'Selected' },
  { value: 'offer_sent', label: 'Offer sent' },
  { value: 'offer_accepted', label: 'Offer accepted' },
  { value: 'offer_declined', label: 'Offer declined' },
  { value: 'hired', label: 'Hired' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'withdrawn', label: 'Withdrawn' },
]

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

export function PipelinePage() {
  const navigate = useNavigate()
  const permissions = usePermissions()
  const list = useListParams({ ordering: '-applied_at' })
  const query = useApplications(list.queryParams)

  const scope = permissions.scopeOf(RESOURCE.APPLICATION)
  const rows = query.data?.data ?? []

  const columns: Array<Column<Application>> = [
    {
      key: 'candidate',
      header: 'Candidate',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.candidate_name} size="sm" />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.candidate_name}</p>
            <p className="truncate text-xs text-ink-muted sm:hidden">{row.job_title}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'job',
      header: 'Role',
      secondary: true,
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate text-ink">{row.job_title}</p>
          <p className="truncate text-xs text-ink-muted">{row.department_name}</p>
        </div>
      ),
    },
    {
      key: 'stage',
      header: 'Stage',
      render: (row) => (
        <span className="inline-flex max-w-[240px] items-center gap-1.5">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: LIME }}
          />
          <Badge tone="info" className="min-w-0" title={row.stage_name}>
            <span className="truncate">{row.stage_name}</span>
          </Badge>
        </span>
      ),
    },
    {
      key: 'verified',
      header: 'Verified',
      secondary: true,
      render: (row) =>
        row.is_verified ? (
          <Badge tone="success">Yes</Badge>
        ) : (
          <span className="text-xs text-ink-subtle">Pending</span>
        ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <ApplicationStatusBadge status={row.status} />,
    },
    {
      key: 'applied',
      header: 'Applied',
      sortKey: 'applied_at',
      align: 'right',
      secondary: true,
      render: (row) => <span className="text-xs text-ink-muted">{formatDate(row.applied_at)}</span>,
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
            Recruitment pipeline
          </span>
        }
        description="Every application you are permitted to see, across all workflows."
      />

      {scope === 'self' && (
        <Banner tone="info" title="You are seeing your assigned candidates">
          Interviewer access shows the applications you have an interview on, and no others.
        </Banner>
      )}
      {scope === 'department' && (
        <Banner tone="info" title="You are seeing your department">
          Applications for jobs outside your department are not included, and are not reachable by
          URL either.
        </Banner>
      )}

      <Section>
        <div className="space-y-4">
          <TableToolbar
            search={list.searchDraft}
            onSearchChange={list.setSearchDraft}
            searchPlaceholder="Search candidates…"
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
              caption="Applications"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/recruitment/applications/${row.id}`)}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={6} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No applications match these filters' : 'No applications yet'}
                  description={
                    list.hasFilters
                      ? 'Try clearing the search or status filter.'
                      : 'Applications appear here once candidates are attached to a published job.'
                  }
                  action={
                    !list.hasFilters && permissions.can(RESOURCE.CANDIDATE) ? (
                      <Link to="/recruitment/candidates">
                        <span className="text-sm font-medium text-brand hover:underline">
                          Go to candidates
                        </span>
                      </Link>
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
    </>
  )
}
