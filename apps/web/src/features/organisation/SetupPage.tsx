/**
 * The setup checklist a new organization lands on.
 *
 * A PROGRESS MARKER, not a staging area. Every step writes to the real domain
 * tables through the screens that already exist, and each one's completion is
 * COMPUTED by the server from those tables rather than stored: closing the
 * browser loses nothing, and deleting the last department honestly reopens
 * that step. There is no wizard state to go stale, which is why this page can
 * be a list of links rather than a form.
 *
 * This is the minimal version, and it is deliberately the whole list plus the
 * finish action -- the stepper UI comes next. What it must already be honest
 * about: which steps are required, which are optional, and that finishing is
 * refused while a required one is outstanding. The server decides all three;
 * this renders them.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '@/app/AuthProvider'
import { apiGet, apiPost } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card, PageHeader } from '@/components/ui/Card'
import { ErrorState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import type { SetupState } from '@/lib/types'

export function SetupPage() {
  const { reloadPermissions } = useAuth()
  const queryClient = useQueryClient()
  const toast = useToast()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['org-setup'],
    queryFn: () => apiGet<SetupState>('/org/setup/'),
  })

  const finish = useMutation({
    mutationFn: () => apiPost('/org/setup/finish/'),
    onSuccess: async () => {
      // The organization's status changes, and the status lives in the
      // permission snapshot -- so the session has to be re-read or the guard
      // will keep sending the administrator back here.
      await reloadPermissions()
      await queryClient.invalidateQueries({ queryKey: ['org-setup'] })
      toast.success('Setup complete. Your organization is now active.')
    },
    onError: () =>
      toast.error('Some required steps are still outstanding.'),
  })

  if (isLoading) return <LoadingBlock label="Loading your setup checklist" />
  if (error || !data) {
    return <ErrorState error={error} onRetry={() => void refetch()} />
  }

  const remaining = data.steps.filter((step) => step.required && !step.complete)

  return (
    <>
      <PageHeader
        title="Finish setting up your organization"
        description={
          `${data.completed} of ${data.total} steps done. Each one writes to the real ` +
          `records, so you can leave and come back — nothing is held in this page.`
        }
      />

      <Card>
        <ol className="divide-y divide-line">
          {data.steps.map((step) => (
            <li key={step.key} className="flex items-center gap-4 py-3">
              <span
                aria-hidden
                className={
                  'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs ' +
                  (step.complete
                    ? 'border-success bg-success/10 text-success'
                    : 'border-line text-ink-muted')
                }
              >
                {step.complete ? '✓' : ''}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">
                  {step.title}
                  {!step.required && (
                    <span className="ml-2 text-xs font-normal text-ink-muted">
                      optional
                    </span>
                  )}
                </p>
                {step.detail && (
                  <p className="text-xs text-ink-muted">{step.detail}</p>
                )}
              </div>
              <Link to={step.route}>
                <Button variant="secondary" size="sm">
                  {step.complete ? 'Review' : 'Set up'}
                </Button>
              </Link>
            </li>
          ))}
        </ol>
      </Card>

      <Card className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-muted">
            {data.can_finish
              ? 'Everything required is in place. Finishing activates your organization.'
              : `Still required: ${remaining.map((step) => step.title).join(', ')}.`}
          </p>
          <Button
            variant="primary"
            // Disabled from the SERVER's verdict rather than a count computed
            // here, and the server refuses the request regardless: the button
            // being enabled is never what makes finishing allowed.
            disabled={!data.can_finish || finish.isPending}
            onClick={() => finish.mutate()}
          >
            {finish.isPending ? 'Finishing…' : 'Finish setup'}
          </Button>
        </div>
      </Card>
    </>
  )
}
