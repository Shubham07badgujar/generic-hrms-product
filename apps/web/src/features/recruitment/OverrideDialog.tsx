/**
 * The administrative override.
 *
 * Rendered distinctly from every other action on purpose. Admin does NOT hold
 * `APPLICATION/REJECT` — a normal rejection returns 403 for this account — and
 * the override writes to its own table, flags the rejection it sets aside
 * rather than editing it, and produces an audit entry naming the actor, the
 * reason and both states.
 *
 * The UI says all of that plainly, because an exception path that looks like
 * the normal one stops being an exception.
 */

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Banner } from '@/components/ui/Misc'
import { Select } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useOverrideDecision } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import type { Application, ApplicationStatus } from '@/lib/types'

const STATUS_OPTIONS: Array<{ value: ApplicationStatus; label: string }> = [
  { value: 'active', label: 'Reopen — back in progress' },
  { value: 'rejected', label: 'Close as rejected' },
  { value: 'withdrawn', label: 'Close as withdrawn' },
  { value: 'selected', label: 'Mark as selected' },
]

export function OverrideAction({
  application,
  onDone,
}: {
  application: Application
  onDone?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const override = useOverrideDecision(application.id)

  const [open, setOpen] = useState(false)
  const [newStatus, setNewStatus] = useState<ApplicationStatus>(
    application.status === 'rejected' ? 'active' : 'rejected',
  )
  const [serverError, setServerError] = useState<string | undefined>()

  if (!permissions.can(RESOURCE.APPLICATION, ACTION.OVERRIDE)) return null

  //: Closed → open. This is the case that moves the application's stage.
  const CLOSED: ApplicationStatus[] = ['rejected', 'withdrawn', 'hired', 'offer_declined']
  const reopening = CLOSED.includes(application.status) && !CLOSED.includes(newStatus)

  return (
    <>
      <Button
        variant="danger-soft"
        onClick={() => {
          setServerError(undefined)
          setOpen(true)
        }}
        leadingIcon={
          <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden>
            <path
              d="M12 3 4.5 6v6c0 4.4 3.1 8.2 7.5 9 4.4-.8 7.5-4.6 7.5-9V6z M12 9v4m0 3h.01"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        }
      >
        Administrative override
      </Button>

      <ReasonDialog
        open={open}
        onClose={() => setOpen(false)}
        loading={override.isPending}
        serverError={serverError}
        title="Administrative override"
        description={`${application.candidate_name} — ${application.job_title}`}
        label="Reason for the override"
        confirmLabel="Record override"
        onSubmit={(reason) =>
          override.mutate(
            { new_status: newStatus, reason },
            {
              onSuccess: () => {
                setOpen(false)
                toast.success('Override recorded', 'The action has been written to the audit log.')
                onDone?.()
              },
              onError: (error) => {
                if (error instanceof ApiError) setServerError(error.displayMessage)
                toast.fromError(error, 'Override not recorded')
              },
            },
          )
        }
        banner={
          <Banner tone="danger" title="This is an exceptional action">
            You are setting aside a decision that belongs to the HR Head. It is stored as an
            override — not as an HR decision — and the audit log will record your name, this reason,
            and both the previous and new state. Any existing rejection is retained and flagged,
            not deleted.
          </Banner>
        }
      >
        <Select
          label="New status"
          required
          value={newStatus}
          onChange={(event) => setNewStatus(event.target.value as ApplicationStatus)}
          options={STATUS_OPTIONS}
          description={`Currently ${application.status.replace(/_/g, ' ')}.`}
        />

        {/* Reopening does more than flip a status: the engine returns the
            application to a stage where a decision can actually be recorded.
            Saying so avoids the Admin expecting a no-op and finding the
            candidate has moved. */}
        {reopening && (
          <Banner tone="info" title="The candidate returns to an actionable stage">
            The workflow engine decides where, from this pipeline's own
            configuration — normally the stage the rejection was taken at. The
            application will accept decisions again, and the original rejection
            stays on the record, flagged as set aside.
          </Banner>
        )}
      </ReasonDialog>
    </>
  )
}
