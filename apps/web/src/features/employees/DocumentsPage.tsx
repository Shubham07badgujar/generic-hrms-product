/**
 * People → Documents. The document management surface.
 *
 * Until now documents were reachable two ways: from the profile of the person
 * they belong to, and — if they were still pending — from a card on the
 * Onboarding page. Both still work and neither is replaced. What was missing
 * was a way to ask a question ACROSS people: what came in this week, whose
 * paperwork is still outstanding, who filed this.
 *
 * Nothing here decides what may be seen. The list is already scoped by the
 * server before it is sent, and every filter narrows that further — a filter
 * can only remove rows, never reach past the scope. The permission checks in
 * this file decide which CONTROLS to render, which is a courtesy to the user
 * and not a security boundary: the server refuses on its own.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardHeader, PageHeader, StatCard } from '@/components/ui/Card'
import { CursorPager, DataTable, TableToolbar, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import {
  useDocumentActions,
  useDocumentTypes,
  useEmployeeDocuments,
} from '@/lib/lifecycleQueries'
import { useDepartments, useEmployees } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError, downloadFile } from '@/lib/api'
import { formatDate, formatDateTime, humanize } from '@/lib/format'
import type { EmployeeDocument } from '@/lib/types'

const STATUS_TONES: Record<string, Tone> = {
  pending: 'warning',
  verified: 'success',
  rejected: 'danger',
  expired: 'neutral',
}

/** Mirrors `MAX_REJECTION_WORDS` in apps/employees/services/documents.py. */
const MAX_REJECTION_WORDS = 250

/** The same count the backend performs, so the hint cannot disagree with the refusal. */
function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length
}

export function DocumentsPage() {
  const permissions = usePermissions()
  const toast = useToast()
  const list = useListParams({ ordering: '-uploaded_at' })

  const query = useEmployeeDocuments(list.queryParams)
  const types = useDocumentTypes()
  const departments = useDepartments()
  const employees = useEmployees({ ordering: 'employee_code' })
  const actions = useDocumentActions()

  const [rejecting, setRejecting] = useState<EmployeeDocument | null>(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | undefined>()

  const mayVerify = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.APPROVE)
  const mayReject = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.REJECT)

  const rows = query.data?.data ?? []
  const words = countWords(reason)
  const overLimit = words > MAX_REJECTION_WORDS

  const closeReject = () => {
    setRejecting(null)
    setReason('')
    setError(undefined)
  }

  const columns: Array<Column<EmployeeDocument>> = [
    {
      key: 'employee',
      header: 'Employee',
      render: (row) => (
        <div className="min-w-0">
          <Link
            to={`/employees/${row.employee}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {row.employee_name}
          </Link>
          <p className="font-mono text-xs text-ink-subtle">{row.employee_code}</p>
        </div>
      ),
    },
    {
      key: 'document_type',
      header: 'Document',
      render: (row) => (
        <div className="min-w-0">
          <p className="text-sm text-ink">{row.document_type_name}</p>
          {row.expires_on && (
            <p className="text-xs text-ink-subtle">expires {formatDate(row.expires_on)}</p>
          )}
        </div>
      ),
    },
    {
      key: 'uploaded_by',
      header: 'Uploaded by',
      secondary: true,
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate text-sm text-ink">{row.uploaded_by_email ?? '—'}</p>
          {/*
            Worth stating rather than leaving to be inferred: HR filing
            paperwork into somebody's record is routine, and is also exactly
            what a reviewer should notice on the occasion it was not.
          */}
          {row.filed_by_someone_else && (
            <Badge tone="info" className="mt-0.5">
              on their behalf
            </Badge>
          )}
        </div>
      ),
    },
    {
      key: 'uploaded_at',
      header: 'Uploaded',
      sortKey: 'uploaded_at',
      secondary: true,
      render: (row) => <span className="text-sm">{formatDate(row.uploaded_at)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortKey: 'status',
      render: (row) => (
        <div className="min-w-0">
          <Badge tone={STATUS_TONES[row.status] ?? 'neutral'}>{humanize(row.status)}</Badge>
          {row.status === 'verified' && row.verified_by_email && (
            <p className="mt-0.5 text-xs text-ink-subtle">
              by {row.verified_by_email}
              {row.verified_at ? ` · ${formatDateTime(row.verified_at)}` : ''}
            </p>
          )}
          {row.status === 'rejected' && (
            <>
              {row.rejected_by_email && (
                <p className="mt-0.5 text-xs text-ink-subtle">
                  by {row.rejected_by_email}
                  {row.rejected_at ? ` · ${formatDateTime(row.rejected_at)}` : ''}
                </p>
              )}
              {row.rejection_reason && (
                <p className="mt-0.5 max-w-xs text-xs text-danger-ink">{row.rejection_reason}</p>
              )}
            </>
          )}
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      headerSrOnly: true,
      align: 'right',
      render: (row) => (
        <div className="flex justify-end gap-2">
          {row.has_file && (
            <Button
              size="sm"
              onClick={() =>
                downloadFile(
                  `/employee-documents/${row.id}/download/`,
                  row.original_filename || 'document',
                )
              }
            >
              Download
            </Button>
          )}
          {mayVerify && row.status === 'pending' && (
            <Button
              size="sm"
              variant="primary"
              onClick={() =>
                actions.verify.mutate(row.id, {
                  onSuccess: () => toast.success('Document verified'),
                  onError: (submitError) => toast.fromError(submitError, 'Could not verify'),
                })
              }
            >
              Verify
            </Button>
          )}
          {mayReject && row.status === 'pending' && (
            <Button size="sm" variant="danger-soft" onClick={() => setRejecting(row)}>
              Reject
            </Button>
          )}
        </div>
      ),
    },
  ]

  if (query.isError) return <ErrorState error={query.error} />

  return (
    <>
      <PageHeader
        title="Documents"
        description="Every document on file, across the people you are permitted to see."
      />

      <div className="mb-5 grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Showing"
          value={String(rows.length)}
          hint={query.data?.meta.next ? 'more on the next page' : undefined}
        />
        <StatCard label="Awaiting verification" value={String(rows.filter((r) => r.status === 'pending').length)} />
        <StatCard label="Filed by someone else" value={String(rows.filter((r) => r.filed_by_someone_else).length)} />
      </div>

      <Card className="space-y-4">
        <CardHeader
          title="All documents"
          description="Uploading, verifying and rejecting are separate permissions — you will only see the actions you hold."
        />

        <TableToolbar
          search={list.searchDraft}
          onSearchChange={list.setSearchDraft}
          searchPlaceholder="Search by name, employee ID or document…"
          onReset={list.reset}
          hasFilters={list.hasFilters}
        >
          <Select
            aria-label="Status"
            value={list.filters.status ?? ''}
            onChange={(event) => list.setParam('status', event.target.value)}
            placeholder="Any status"
            options={[
              { value: 'pending', label: 'Pending' },
              { value: 'verified', label: 'Verified' },
              { value: 'rejected', label: 'Rejected' },
              { value: 'expired', label: 'Expired' },
            ]}
          />
          <Select
            aria-label="Document type"
            value={list.filters.document_type ?? ''}
            onChange={(event) => list.setParam('document_type', event.target.value)}
            placeholder="Any type"
            options={(types.data ?? []).map((type) => ({ value: type.id, label: type.name }))}
          />
          <Select
            aria-label="Department"
            value={list.filters.department ?? ''}
            onChange={(event) => list.setParam('department', event.target.value)}
            placeholder="Any department"
            options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
          />
          <Select
            aria-label="Employee"
            value={list.filters.employee ?? ''}
            onChange={(event) => list.setParam('employee', event.target.value)}
            placeholder="Any employee"
            options={(employees.data?.data ?? []).map((e) => ({
              value: e.id,
              label: `${e.full_name} (${e.employee_code})`,
            }))}
          />
          <Select
            aria-label="Uploaded by"
            value={list.filters.uploaded_by_employee ?? ''}
            onChange={(event) => list.setParam('uploaded_by_employee', event.target.value)}
            placeholder="Any uploader"
            options={(employees.data?.data ?? []).map((e) => ({
              value: e.id,
              label: `${e.full_name} (${e.employee_code})`,
            }))}
          />
          <TextInput
            type="date"
            aria-label="Uploaded on or after"
            value={list.filters.uploaded_after ?? ''}
            onChange={(event) => list.setParam('uploaded_after', event.target.value)}
          />
          <TextInput
            type="date"
            aria-label="Uploaded on or before"
            value={list.filters.uploaded_before ?? ''}
            onChange={(event) => list.setParam('uploaded_before', event.target.value)}
          />
        </TableToolbar>

        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
          sort={list.sort}
          onSortChange={list.setSort}
          isLoading={query.isLoading}
          loadingState={<TableSkeleton columns={6} />}
          emptyState={
            <EmptyState
              title={list.hasFilters ? 'Nothing matches those filters' : 'No documents on file yet'}
              description={
                list.hasFilters
                  ? 'Clear the filters to see everything you are permitted to see.'
                  : 'Documents appear here once somebody uploads one from a profile.'
              }
            />
          }
          footer={
            <CursorPager
              hasPrevious={Boolean(query.data?.meta.previous)}
              hasNext={Boolean(query.data?.meta.next)}
              onPrevious={() => list.setCursor(query.data?.meta.previous ?? '')}
              onNext={() => list.setCursor(query.data?.meta.next ?? '')}
            />
          }
        />
      </Card>

      <Modal
        open={Boolean(rejecting)}
        onClose={closeReject}
        busy={actions.reject.isPending}
        title="Reject this document"
        description="The employee is told, and given the reason. This cannot be reworded afterwards."
        footer={
          <>
            <Button onClick={closeReject} disabled={actions.reject.isPending}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={actions.reject.isPending}
              disabled={!reason.trim() || overLimit}
              onClick={() => {
                setError(undefined)
                actions.reject.mutate(
                  { id: rejecting!.id, reason: reason.trim() },
                  {
                    onSuccess: () => {
                      toast.success('Document rejected', 'The employee has been notified.')
                      closeReject()
                    },
                    onError: (submitError) => {
                      if (submitError instanceof ApiError) setError(submitError.displayMessage)
                      toast.fromError(submitError, 'Could not reject')
                    },
                  },
                )
              }}
            >
              Reject document
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          {rejecting && (
            <Banner tone="info">
              {rejecting.document_type_name} — {rejecting.employee_name} ({rejecting.employee_code})
            </Banner>
          )}
          <TextArea
            label="Reason"
            required
            rows={6}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Say what is wrong and what a good copy would look like."
            hint={
              overLimit
                ? `${words} words — the limit is ${MAX_REJECTION_WORDS}.`
                : `${words} of ${MAX_REJECTION_WORDS} words.`
            }
            error={overLimit ? `Shorten this to ${MAX_REJECTION_WORDS} words or fewer.` : undefined}
          />
          <p className="text-xs text-ink-subtle">
            The employee sees this reason in their notifications. A corrected document arrives as a
            new submission — this rejection stays on the record.
          </p>
        </div>
      </Modal>
    </>
  )
}
