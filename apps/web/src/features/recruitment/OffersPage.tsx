/** Offers across the pipeline. Actions live on the application, where the context is. */

import { Link } from 'react-router-dom'
import { PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { OfferStatusBadge } from '@/components/ui/Badge'
import { Avatar } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { useOffers } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/format'
import type { Offer } from '@/lib/types'

const STATUS_OPTIONS = [
  { value: 'draft', label: 'Draft' },
  { value: 'sent', label: 'Sent' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'declined', label: 'Declined' },
  { value: 'withdrawn', label: 'Withdrawn' },
]

export function OffersPage() {
  const list = useListParams({ ordering: '-created_at' })
  const query = useOffers(list.queryParams)
  const rows = query.data?.data ?? []

  const columns: Array<Column<Offer>> = [
    {
      key: 'candidate',
      header: 'Candidate',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.candidate_name} size="sm" />
          <span className="truncate font-medium text-ink">{row.candidate_name}</span>
        </div>
      ),
    },
    {
      key: 'ctc',
      header: 'Annual CTC',
      align: 'right',
      render: (row) => formatCurrency(row.offered_ctc),
    },
    {
      key: 'joining',
      header: 'Joining',
      secondary: true,
      render: (row) => formatDate(row.joining_date),
    },
    { key: 'status', header: 'Status', render: (row) => <OfferStatusBadge status={row.status} /> },
    {
      key: 'sent',
      header: 'Sent',
      secondary: true,
      align: 'right',
      render: (row) => (
        <span className="text-xs text-ink-muted">{formatDateTime(row.sent_at)}</span>
      ),
    },
    {
      key: 'open',
      header: 'Open',
      headerSrOnly: true,
      align: 'right',
      render: (row) => (
        <Link to={`/recruitment/applications/${row.application}`}>
          <Button size="sm">Open</Button>
        </Link>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Offers"
        description="Creating, sending and recording responses happens on the candidate's application."
      />

      <Section>
        <div className="space-y-4">
          <TableToolbar onReset={list.reset} hasFilters={list.hasFilters}>
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
              caption="Offers"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={6} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No offers match' : 'No offers yet'}
                  description="An offer can be raised once the HR Head has selected a candidate."
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
