/**
 * The pipeline visualisation.
 *
 * EVERY STAGE COMES FROM THE API. There is no Therapist component and no
 * Office Boy component — this reads `/workflows/{id}/` and renders whatever
 * stages that workflow owns, in `order`, with the responsible role and the
 * allowed decisions the backend declares. A third pipeline added as
 * configuration renders here with no frontend change at all, which is the
 * whole point of the configurable engine and would be quietly undone by one
 * hard-coded stage list.
 *
 * The terminal stages (Hired / Rejected) are drawn out of the main track,
 * because they are outcomes rather than steps and putting them in the line
 * suggests every candidate passes through both.
 */

import clsx from 'clsx'
import { Badge, STAGE_KIND_LABELS } from '@/components/ui/Badge'
import { roleLabel } from '@/app/AppShell'
import type { HiringWorkflow, WorkflowStage, UUID } from '@/lib/types'

type StageState = 'done' | 'current' | 'upcoming' | 'skipped'

function stateOf(
  stage: WorkflowStage,
  currentOrder: number | null,
  closed: boolean,
  hired: boolean,
): StageState {
  // A hire completes the WHOLE track. The application's current stage stops
  // wherever the conversion happened (usually Offer), so without this the
  // final stages sat grey under a candidate the page itself calls Hired.
  if (hired) return 'done'
  if (currentOrder === null) return 'upcoming'
  if (stage.order === currentOrder) return closed ? 'skipped' : 'current'
  return stage.order < currentOrder ? 'done' : 'upcoming'
}

const DOT_STYLES: Record<StageState, string> = {
  done: 'bg-success text-white border-success',
  current: 'bg-brand text-white border-brand ring-4 ring-brand/15',
  upcoming: 'bg-surface text-ink-subtle border-line',
  skipped: 'bg-canvas text-ink-subtle border-line',
}

function StageDot({ state, index }: { state: StageState; index: number }) {
  return (
    <span
      className={clsx(
        'relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2',
        'text-2xs font-semibold transition-colors',
        DOT_STYLES[state],
      )}
      aria-hidden
    >
      {state === 'done' ? (
        <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
          <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        index + 1
      )}
    </span>
  )
}

export function WorkflowStepper({
  workflow,
  currentStageId,
  closed = false,
  hired = false,
  orientation = 'vertical',
}: {
  workflow: HiringWorkflow
  currentStageId?: UUID | null
  closed?: boolean
  /** The application ended in a hire: the whole track renders as done. */
  hired?: boolean
  orientation?: 'vertical' | 'horizontal'
}) {
  const track = workflow.stages
    .filter((stage) => !stage.is_terminal)
    .sort((a, b) => a.order - b.order)
  const terminals = workflow.stages.filter((stage) => stage.is_terminal)

  const current = workflow.stages.find((stage) => stage.id === currentStageId) ?? null
  const currentOrder = current ? current.order : null

  if (orientation === 'horizontal') {
    return (
      <div className="scrollbar-thin overflow-x-auto pb-2">
        <ol className="flex min-w-max items-start gap-0">
          {track.map((stage, index) => {
            const state = stateOf(stage, currentOrder, closed, hired)
            return (
              <li key={stage.id} className="flex items-start">
                <div className="flex w-32 flex-col items-center gap-1.5 px-1 text-center">
                  <StageDot state={state} index={index} />
                  <span
                    className={clsx(
                      'text-xs font-medium leading-tight',
                      state === 'current' ? 'text-brand' : state === 'done' ? 'text-ink' : 'text-ink-subtle',
                    )}
                  >
                    {stage.name}
                  </span>
                </div>
                {index < track.length - 1 && (
                  <span
                    className={clsx(
                      'mt-3.5 h-0.5 w-6 shrink-0 rounded-full',
                      state === 'done' ? 'bg-success' : 'bg-line',
                    )}
                    aria-hidden
                  />
                )}
              </li>
            )
          })}
        </ol>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <ol className="relative">
        {track.map((stage, index) => {
          const state = stateOf(stage, currentOrder, closed, hired)
          return (
            <li key={stage.id} className="relative flex gap-3 pb-4 last:pb-0">
              {index < track.length - 1 && (
                <span
                  className={clsx(
                    'absolute left-[13px] top-7 h-[calc(100%-1.25rem)] w-0.5',
                    state === 'done' ? 'bg-success/40' : 'bg-line',
                  )}
                  aria-hidden
                />
              )}
              <StageDot state={state} index={index} />
              <div className="min-w-0 flex-1 pt-0.5">
                <div className="flex flex-wrap items-center gap-2">
                  <p
                    className={clsx(
                      'text-sm font-medium',
                      state === 'current' ? 'text-brand' : 'text-ink',
                    )}
                  >
                    {stage.name}
                  </p>
                  {state === 'current' && !closed && <Badge tone="brand">Current</Badge>}
                  {stage.is_final_hr_decision && <Badge tone="warning">HR decision</Badge>}
                </div>
                <p className="mt-0.5 text-xs text-ink-muted">
                  {STAGE_KIND_LABELS[stage.kind] ?? stage.kind}
                  {stage.responsible_role_code && ` · ${roleLabel(stage.responsible_role_code)}`}
                  {stage.requires_interview && ' · interview required'}
                </p>
              </div>
            </li>
          )
        })}
      </ol>

      {terminals.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-line pt-3">
          <span className="text-xs text-ink-subtle">Outcomes:</span>
          {terminals.map((stage) => (
            <Badge key={stage.id} tone={stage.is_won ? 'success' : 'danger'}>
              {stage.name}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}
