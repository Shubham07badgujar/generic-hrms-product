/**
 * A linear progress indicator for a multi-step flow.
 *
 * Distinct from `WorkflowStepper`, which visualises a SERVER-DEFINED hiring
 * workflow and derives its state from a candidate's current stage. This one
 * describes a client-side sequence and knows nothing about recruitment.
 *
 * Rendered as an ordered list so the structure is real to a screen reader
 * rather than implied by styling, with the current step carrying
 * `aria-current`.
 */

import clsx from 'clsx'

export interface Step {
  id: string
  label: string
}

interface StepperProps {
  steps: Step[]
  /** Index of the step being worked on. */
  current: number
  className?: string
}

function Dot({ state, index }: { state: 'done' | 'current' | 'upcoming'; index: number }) {
  return (
    <span
      className={clsx(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold',
        state === 'done' && 'border-brand bg-brand text-white',
        state === 'current' && 'border-brand bg-surface text-brand',
        state === 'upcoming' && 'border-line bg-surface text-ink-subtle',
      )}
      aria-hidden
    >
      {state === 'done' ? (
        <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none">
          <path
            d="M5 10.5l3.5 3.5L15 7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        index + 1
      )}
    </span>
  )
}

export function Stepper({ steps, current, className }: StepperProps) {
  return (
    <nav aria-label="Progress" className={className}>
      <ol className="flex items-center gap-1 overflow-x-auto pb-1">
        {steps.map((step, index) => {
          const state = index < current ? 'done' : index === current ? 'current' : 'upcoming'
          return (
            <li key={step.id} className="flex shrink-0 items-center gap-2">
              <span
                className="flex items-center gap-2"
                aria-current={state === 'current' ? 'step' : undefined}
              >
                <Dot state={state} index={index} />
                <span
                  className={clsx(
                    'whitespace-nowrap text-sm',
                    state === 'upcoming' ? 'text-ink-subtle' : 'font-medium text-ink',
                  )}
                >
                  {step.label}
                </span>
              </span>
              {index < steps.length - 1 && (
                <span
                  className={clsx(
                    'mx-2 h-px w-8',
                    index < current ? 'bg-brand' : 'bg-line',
                  )}
                  aria-hidden
                />
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
