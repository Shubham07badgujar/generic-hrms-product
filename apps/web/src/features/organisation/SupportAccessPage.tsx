/**
 * Operators asking to see this organization's configuration, and the answer.
 *
 * THE CUSTOMER DECIDES. A SaaS operator has no implicit reach into this
 * organization's data; when support needs to look at how leave or roles are
 * set up, they ask here, with a reason, and nothing is visible until an
 * administrator approves. An approval lasts 24 hours and can be revoked at any
 * moment before that.
 *
 * What an approval shows is fixed in the product, not chosen per request:
 * structure, roles and permissions, leave and attendance policies, hiring
 * workflows, document types, salary components. Never employees, salaries,
 * payslips, candidates or documents. The page says so, because "what am I
 * agreeing to" is the question an administrator is actually asking.
 *
 * Deciding needs ORG_SETTINGS/EDIT, the same authority as changing the
 * settings a grant would expose. The server enforces it; the buttons merely
 * follow it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { usePermissions } from '@/app/AuthProvider'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, PageHeader } from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { apiGet, apiPost } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { ACTION, RESOURCE } from '@/lib/permissions'
import type { SupportGrant } from '@/lib/types'

const STATUS: Record<SupportGrant['status'], { label: string; tone: Tone }> = {
  requested: { label: 'Awaiting your decision', tone: 'warning' },
  approved: { label: 'Approved', tone: 'success' },
  denied: { label: 'Denied', tone: 'neutral' },
  revoked: { label: 'Ended', tone: 'neutral' },
}

type Decision = 'approve' | 'deny' | 'revoke'

export function SupportAccessPage() {
  const permissions = usePermissions()
  const canDecide = permissions.can(RESOURCE.ORG_SETTINGS, ACTION.EDIT)
  const queryClient = useQueryClient()
  const toast = useToast()

  const grants = useQuery({
    queryKey: ['org-support-grants'],
    queryFn: () => apiGet<SupportGrant[]>('/org/support-grants/'),
  })

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: Decision }) =>
      apiPost<SupportGrant>(`/org/support-grants/${id}/${decision}/`),
    onSuccess: (_grant, { decision }) => {
      void queryClient.invalidateQueries({ queryKey: ['org-support-grants'] })
      toast.success(
        decision === 'approve'
          ? 'Access approved for 24 hours.'
          : decision === 'deny'
            ? 'Request denied.'
            : 'Access ended.',
      )
    },
    onError: (error) => toast.fromError(error, 'The decision was not recorded.'),
  })

  return (
    <>
      <PageHeader
        title="Support access"
        description="Requests from the platform's support team to see how this organization is configured. Nothing is visible to them unless you approve."
      />

      <Card>
        <p className="text-sm text-ink-muted">
          An approval shows <strong className="text-ink">configuration only</strong> —
          departments, locations and designations, roles and permissions, leave and shift
          policies, hiring workflows, document types and salary components. It never shows
          employees, salaries, payslips, candidates or documents. It lasts 24 hours, you can end
          it sooner, and every look is recorded in your audit log.
        </p>
      </Card>

      {grants.isLoading ? (
        <LoadingBlock label="Loading requests" />
      ) : grants.error ? (
        <ErrorState error={grants.error} onRetry={() => void grants.refetch()} />
      ) : !grants.data?.length ? (
        <EmptyState
          title="No requests"
          description="Support has never asked to see this organization's configuration."
        />
      ) : (
        <ul className="space-y-3">
          {grants.data.map((grant) => {
            const status = STATUS[grant.status]
            const live = grant.status === 'approved' && grant.usable
            return (
              <li key={grant.id}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <p className="text-sm font-medium text-ink">{grant.requested_by_email}</p>
                      <p className="text-sm text-ink-muted">“{grant.reason}”</p>
                      <p className="text-xs text-ink-subtle">
                        Asked {formatDateTime(grant.created_at)}
                        {live && grant.expires_at
                          ? ` · access ends ${formatDateTime(grant.expires_at)}`
                          : ''}
                      </p>
                    </div>
                    <Badge tone={live || grant.status !== 'approved' ? status.tone : 'neutral'}>
                      {grant.status === 'approved' && !live ? 'Expired' : status.label}
                    </Badge>
                  </div>
                  {canDecide && (grant.status === 'requested' || live) && (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {grant.status === 'requested' ? (
                        <>
                          <Button
                            variant="primary"
                            loading={decide.isPending}
                            onClick={() => decide.mutate({ id: grant.id, decision: 'approve' })}
                          >
                            Approve for 24 hours
                          </Button>
                          <Button
                            variant="secondary"
                            disabled={decide.isPending}
                            onClick={() => decide.mutate({ id: grant.id, decision: 'deny' })}
                          >
                            Deny
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="danger-soft"
                          loading={decide.isPending}
                          onClick={() => decide.mutate({ id: grant.id, decision: 'revoke' })}
                        >
                          End access now
                        </Button>
                      )}
                    </div>
                  )}
                </Card>
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
