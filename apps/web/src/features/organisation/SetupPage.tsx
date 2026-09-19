/**
 * The setup wizard a new organization lands on.
 *
 * A PROGRESS MARKER, NOT A STAGING AREA, and every decision here follows from
 * that. Each step is done on the screen that already owns it — departments on
 * the organisation page, leave types under settings — writing to the real
 * domain tables through endpoints that existed before this wizard did. Nothing
 * is held here and submitted later.
 *
 * So completion is COMPUTED by the server from those tables on every read,
 * never stored. Closing the browser loses nothing. Deleting the last
 * department honestly reopens that step. There is no per-step row, no stored
 * form state and no transition machine to fall out of step with the data —
 * which is also why this page can refetch on every visit and simply be right.
 *
 * WHAT THE WIZARD ADDS over the list it replaces: one obvious next action.
 * Ten steps presented flat is a checklist somebody reads top to bottom and
 * loses their place in; a stepper names the one to do now, keeps the rest
 * visible for context, and lets an administrator jump if their company needs
 * locations before departments.
 *
 * FINISHING IS THE SERVER'S DECISION. The button reads `can_finish` and the
 * endpoint re-checks it, refusing with the steps still outstanding. A disabled
 * button is a courtesy; it is not what makes finishing allowed.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '@/app/AuthProvider'
import { apiGet, apiPost } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card, PageHeader } from '@/components/ui/Card'
import { ErrorState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import type { SetupState, SetupStep } from '@/lib/types'

/** Done, the one to do now, or still ahead. */
type StepState = 'complete' | 'current' | 'upcoming'

function stateOf(step: SetupStep, current: SetupStep | undefined): StepState {
  if (step.complete) return 'complete'
  return step === current ? 'current' : 'upcoming'
}

const MARKER: Record<StepState, string> = {
  complete: 'border-success bg-success-soft text-success-ink',
  current: 'border-brand bg-brand text-white',
  upcoming: 'border-line text-ink-muted',
}

function StepRow({
  step,
  index,
  state,
}: {
  step: SetupStep
  index: number
  state: StepState
}) {
  return (
    <li className="flex items-start gap-4 py-4">
      <span
        aria-hidden
        className={
          'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ' +
          'text-xs font-medium ' +
          MARKER[state]
        }
      >
        {state === 'complete' ? '✓' : index + 1}
      </span>

      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
          {step.title}
          {!step.required && (
            <span className="rounded-full bg-canvas px-2 py-0.5 text-[11px] font-normal text-ink-muted">
              optional
            </span>
          )}
          {state === 'current' && (
            <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand-ink">
              next
            </span>
          )}
        </p>
        {step.detail && (
          <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{step.detail}</p>
        )}
      </div>

      <Link to={step.route} className="shrink-0">
        <Button variant={state === 'current' ? 'primary' : 'secondary'} size="sm">
          {step.complete ? 'Review' : 'Set up'}
        </Button>
      </Link>
    </li>
  )
}

export function SetupPage() {
  const { reloadPermissions } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['org-setup'],
    queryFn: () => apiGet<SetupState>('/org/setup/'),
    // Always refetched on arrival. An administrator reaches this page by
    // coming BACK from the screen where they just did a step, so a cached
    // answer would show the work they have already done as outstanding.
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
  })

  const finish = useMutation({
    mutationFn: () => apiPost('/org/setup/finish/'),
    onSuccess: async () => {
      // The organization's status lives in the permission snapshot, and the
      // guard reads it. Without re-reading, finishing would bounce the
      // administrator straight back to the checklist they just completed.
      await reloadPermissions()
      await queryClient.invalidateQueries({ queryKey: ['org-setup'] })
      toast.success(
        'Setup complete',
        'Your organization is active. Everything is where you left it.',
      )
      navigate('/')
    },
    // The server's own words, which name the steps it refused for. A generic
    // message here would be less useful than the 422 already is.
    onError: (failure) => toast.fromError(failure, 'Setup cannot be finished yet'),
  })

  if (isLoading) return <LoadingBlock label="Loading your setup checklist" />
  if (error || !data) return <ErrorState error={error} onRetry={() => void refetch()} />

  // The first unfinished REQUIRED step is the one to do now; when only
  // optional ones remain, the next action is finishing rather than another
  // step, so nothing is marked current.
  const current = data.steps.find((step) => step.required && !step.complete)
  const outstanding = data.steps.filter((step) => step.required && !step.complete)
  const percent = data.total ? Math.round((data.completed / data.total) * 100) : 0

  return (
    <>
      <PageHeader
        title="Set up your organization"
        description={
          'Each step is done on its own screen and saved as you go — leave and come ' +
          'back whenever you like. Nothing here is held until the end.'
        }
      />

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium text-ink">
            {data.completed} of {data.total} complete
          </p>
          <p className="text-xs text-ink-muted">
            {outstanding.length === 0
              ? 'Everything required is done.'
              : `${outstanding.length} required step${outstanding.length === 1 ? '' : 's'} left`}
          </p>
        </div>
        <div
          className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-canvas"
          role="progressbar"
          aria-valuenow={data.completed}
          aria-valuemin={0}
          aria-valuemax={data.total}
          aria-label="Setup progress"
        >
          <div
            className="h-full rounded-full bg-brand transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
      </Card>

      <Card className="mt-4">
        <ol className="divide-y divide-line">
          {data.steps.map((step, index) => (
            <StepRow
              key={step.key}
              step={step}
              index={index}
              state={stateOf(step, current)}
            />
          ))}
        </ol>
      </Card>

      <Card className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-prose text-sm text-ink-muted">
            {data.can_finish
              ? 'Finishing activates your organization. You can still change any of ' +
                'this afterwards — these screens do not go away.'
              : `Still required: ${outstanding.map((step) => step.title).join(', ')}.`}
          </p>
          <Button
            variant="primary"
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
