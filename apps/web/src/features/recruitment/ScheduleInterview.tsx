/**
 * Interview scheduling — layer 3 of the three-layer double-booking guard.
 *
 * This form calls `/interviews/check-conflict/` as the time changes and blocks
 * Save when it reports a clash. That is a COURTESY LAYER and nothing more:
 *
 *   layer 1  PostgreSQL `EXCLUDE USING gist` on (interviewer, time range)
 *   layer 2  the service's locked overlap query, giving a readable message
 *   layer 3  this check, so the clash is visible before submitting
 *
 * Between this check and the submit the slot can still be taken by someone
 * else. When that happens the API returns 400 naming the clashing interview,
 * and the form shows it — which is correct behaviour, not a failure of the
 * check. Weakening layers 1 and 2 on the strength of this one is exactly the
 * mistake the backend was built to make impossible.
 */

import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Modal'
import { Select, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { useToast } from '@/components/ui/Toast'
import { useEligibleInterviewers, useScheduleInterview } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { useConflictCheck } from '@/lib/queries'
import { formatDateTime } from '@/lib/format'
import { roleLabel } from '@/app/AppShell'
import type { Application, WorkflowStage } from '@/lib/types'

const DURATIONS = [15, 30, 45, 60, 90, 120]

/** A Date as the min a datetime-local input takes, in local time. */
function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

export function ScheduleInterviewDrawer({
  open,
  onClose,
  application,
  stage,
  onScheduled,
  initialScheduledAt,
}: {
  open: boolean
  onClose: () => void
  application: Application
  stage: WorkflowStage
  onScheduled?: () => void
  /** Pre-fill from a candidate-chosen slot; still editable, still conflict-checked. */
  initialScheduledAt?: string
}) {
  const toast = useToast()
  const schedule = useScheduleInterview()

  const [interviewer, setInterviewer] = useState('')
  const [scheduledAt, setScheduledAt] = useState('')

  useEffect(() => {
    if (open && initialScheduledAt) setScheduledAt(initialScheduledAt)
  }, [open, initialScheduledAt])
  const [duration, setDuration] = useState(45)
  const [mode, setMode] = useState('in_person')
  const [locationOrLink, setLocationOrLink] = useState('')
  const [serverError, setServerError] = useState<string | undefined>()

  /*
   * Who may take this round is the SERVER's answer, not a filter over the
   * employee directory. The directory is scoped to what the caller may see,
   * so an interviewer in another department disappeared from the picker and
   * the round could not be booked by anyone whose scope excluded them. The
   * endpoint asks the stage instead: its role, still-employed holders.
   */
  const pool = useEligibleInterviewers(open ? stage.id : undefined)
  const eligible = pool.data?.data ?? []

  const endsAt = useMemo(() => {
    if (!scheduledAt) return undefined
    const start = new Date(scheduledAt)
    if (Number.isNaN(start.getTime())) return undefined
    return new Date(start.getTime() + duration * 60_000).toISOString()
  }, [scheduledAt, duration])

  const conflict = useConflictCheck({
    interviewer: interviewer || undefined,
    start: scheduledAt ? new Date(scheduledAt).toISOString() : undefined,
    end: endsAt,
  })

  const hasConflict = conflict.data?.conflict === true
  const ready = Boolean(interviewer && scheduledAt) && !hasConflict

  function reset() {
    setInterviewer('')
    setScheduledAt('')
    setDuration(45)
    setMode('in_person')
    setLocationOrLink('')
    setServerError(undefined)
  }

  function submit() {
    setServerError(undefined)
    schedule.mutate(
      {
        application: application.id,
        stage: stage.id,
        interviewer,
        scheduled_at: new Date(scheduledAt).toISOString(),
        duration_minutes: duration,
        mode,
        location_or_link: locationOrLink,
      },
      {
        onSuccess: () => {
          toast.success('Interview scheduled')
          reset()
          onClose()
          onScheduled?.()
        },
        onError: (error) => {
          // A clash that appeared between the check and the submit lands here.
          if (error instanceof ApiError) setServerError(error.displayMessage)
          toast.fromError(error, 'Could not schedule')
        },
      },
    )
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      busy={schedule.isPending}
      title="Schedule interview"
      description={`${application.candidate_name} — ${stage.name}`}
      footer={
        <>
          <Button onClick={onClose} disabled={schedule.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={schedule.isPending} disabled={!ready}>
            Schedule
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {serverError && (
          <Banner tone="danger" title="The server refused this booking">
            {serverError}
          </Banner>
        )}

        <Select
          label="Interviewer"
          required
          value={interviewer}
          onChange={(event) => setInterviewer(event.target.value)}
          placeholder="Select an interviewer"
          options={eligible.map((employee) => ({
            value: employee.id,
            label: `${employee.full_name} — ${employee.department_name || 'No department'}`,
          }))}
          description={
            stage.responsible_role_code
              ? `This stage requires the ${roleLabel(stage.responsible_role_code)} role.`
              : undefined
          }
        />

        {eligible.length === 0 && !pool.isLoading && pool.data?.problem && (
          <Banner tone="warning" title="This round has no interviewer">
            {pool.data.problem}
          </Banner>
        )}

        <TextInput
          label="Date and time"
          type="datetime-local"
          required
          min={toLocalInputValue(new Date())}
          value={scheduledAt}
          onChange={(event) => setScheduledAt(event.target.value)}
        />

        <Select
          label="Duration"
          value={String(duration)}
          onChange={(event) => setDuration(Number(event.target.value))}
          options={DURATIONS.map((minutes) => ({
            value: String(minutes),
            label: `${minutes} minutes`,
          }))}
        />

        {/* The live conflict indicator. */}
        {interviewer && scheduledAt && (
          <div aria-live="polite">
            {conflict.isFetching ? (
              <Banner tone="neutral">Checking the interviewer's calendar…</Banner>
            ) : hasConflict && conflict.data?.interview ? (
              <Banner tone="danger" title="This interviewer is already booked">
                {conflict.data.interview.candidate_name} —{' '}
                {formatDateTime(conflict.data.interview.scheduled_at)} to{' '}
                {formatDateTime(conflict.data.interview.scheduled_end).split(', ')[1]}. Choose
                another time or another interviewer.
              </Banner>
            ) : conflict.data ? (
              <Banner tone="success" title="The slot is free">
                No overlapping interview for this interviewer. The database re-checks this on save.
              </Banner>
            ) : null}
          </div>
        )}

        <Select
          label="Mode"
          value={mode}
          onChange={(event) => setMode(event.target.value)}
          options={[
            { value: 'in_person', label: 'In person' },
            { value: 'video', label: 'Video call' },
            { value: 'phone', label: 'Telephone' },
          ]}
        />

        <TextInput
          label={mode === 'in_person' ? 'Location' : 'Meeting link'}
          value={locationOrLink}
          onChange={(event) => setLocationOrLink(event.target.value)}
          placeholder={mode === 'in_person' ? 'Room 2, Ground floor' : 'https://…'}
        />
      </div>
    </Drawer>
  )
}
