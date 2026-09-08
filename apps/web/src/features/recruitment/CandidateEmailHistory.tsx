/**
 * What the candidate has been emailed about this application, and the retry.
 *
 * Reads the communication log the backend writes when a transition fires an
 * email. Nothing here composes or sends anything itself — the button asks the
 * server to try a FAILED or SKIPPED row again; a SENT row has no button, and
 * the server refuses to resend one regardless.
 *
 * Shown to whoever can see the application (VIEW). Retry needs EDIT, and the
 * button is hidden without it because a button that always 403s is a worse
 * experience than none — the API is what actually decides.
 */

import { useState } from 'react'
import { Card, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { useApplicationNotifications, useRetryNotification } from '@/lib/queries'
import { formatDateTime } from '@/lib/format'
import type { CandidateNotification, CandidateNotificationStatus, UUID } from '@/lib/types'

const TONE: Record<CandidateNotificationStatus, 'success' | 'danger' | 'warning' | 'neutral'> = {
  sent: 'success',
  failed: 'danger',
  skipped: 'warning',
  pending: 'neutral',
}

export function CandidateEmailHistory({
  applicationId,
  mayRetry,
}: {
  applicationId: UUID
  mayRetry: boolean
}) {
  const toast = useToast()
  const query = useApplicationNotifications(applicationId)
  const retry = useRetryNotification(applicationId)
  const [open, setOpen] = useState<UUID | null>(null)
  const rows = query.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Email / notification history"
        description="Every email sent to the candidate about this application, with its delivery status."
      />
      {query.isLoading ? (
        <LoadingBlock />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState compact title="Nothing sent yet" description="Emails appear here as the application moves through the workflow." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="email-history">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-subtle">
                <th className="py-1.5 pr-3 font-medium">Date</th>
                <th className="py-1.5 pr-3 font-medium">Event</th>
                <th className="py-1.5 pr-3 font-medium">Status</th>
                <th className="py-1.5 pr-3 font-medium">Recipient</th>
                <th className="py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <Row
                  key={row.id}
                  row={row}
                  expanded={open === row.id}
                  onToggle={() => setOpen(open === row.id ? null : row.id)}
                  mayRetry={mayRetry}
                  retrying={retry.isPending && retry.variables === row.id}
                  onRetry={() =>
                    retry.mutate(row.id, {
                      onSuccess: (updated) =>
                        updated.status === 'sent'
                          ? toast.success('Email sent', `${updated.kind_display} → ${updated.recipient_email}`)
                          : toast.error('Still not delivered', updated.error || updated.status_display),
                      onError: (error) => toast.fromError(error, 'Could not retry'),
                    })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function Row({
  row,
  expanded,
  onToggle,
  mayRetry,
  retrying,
  onRetry,
}: {
  row: CandidateNotification
  expanded: boolean
  onToggle: () => void
  mayRetry: boolean
  retrying: boolean
  onRetry: () => void
}) {
  const retryable = mayRetry && (row.status === 'failed' || row.status === 'skipped')
  return (
    <>
      <tr className="align-top">
        <td className="whitespace-nowrap py-2 pr-3 text-xs text-ink-muted">
          {formatDateTime(row.sent_at ?? row.last_attempt_at ?? row.created_at)}
        </td>
        <td className="py-2 pr-3">
          <button
            type="button"
            onClick={onToggle}
            className="text-left font-medium text-ink hover:underline"
            aria-expanded={expanded}
          >
            {row.kind_display}
          </button>
          <p className="truncate text-xs text-ink-subtle">{row.subject}</p>
        </td>
        <td className="py-2 pr-3">
          <Badge tone={TONE[row.status]}>{row.status_display}</Badge>
          {row.attempts > 1 && (
            <span className="ml-1 text-xs text-ink-subtle">×{row.attempts}</span>
          )}
        </td>
        <td className="py-2 pr-3 text-xs text-ink-muted">{row.recipient_email || 'Candidate (no email)'}</td>
        <td className="py-2 text-right">
          {retryable && (
            <Button size="sm" loading={retrying} onClick={onRetry}>
              Retry
            </Button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} className="pb-3">
            <div className="rounded-lg border border-line bg-canvas p-3">
              {row.error && (
                <p className="mb-2 text-xs text-danger">
                  <span className="font-medium">Failure:</span> {row.error}
                </p>
              )}
              <pre className="whitespace-pre-wrap font-sans text-xs text-ink-muted">{row.body_text}</pre>
              {row.triggered_by_email && (
                <p className="mt-2 text-xs text-ink-subtle">Triggered by {row.triggered_by_email}</p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
