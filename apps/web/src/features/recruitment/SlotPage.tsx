/**
 * The candidate's interview slot selection: /interview-slot/:token.
 *
 * Bare and anonymous, like the application form. The token names one round of
 * one application; the page shows the offered windows and takes exactly one
 * choice, which the server validates against what it offered. After choosing,
 * the candidate is told HR will confirm — the final schedule (with the
 * meeting link) arrives by email once someone books the interview.
 */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import axios from 'axios'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Banner } from '@/components/ui/Misc'
import { LoadingBlock } from '@/components/ui/States'
import { anonymousClient } from './ApplyPage'

interface SlotOption {
  start: string
  end: string
}

interface InviteSummary {
  job_title: string
  round_name: string
  candidate_name: string
  status: string
  open: boolean
  options: SlotOption[]
  selected: SlotOption | null
}

function label(option: SlotOption): string {
  const start = new Date(option.start)
  const end = new Date(option.end)
  const day = start.toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  return `${day} · ${time(start)} – ${time(end)}`
}

export function SlotPage() {
  const { token = '' } = useParams<{ token: string }>()
  const [choice, setChoice] = useState<SlotOption | null>(null)
  const [error, setError] = useState<string | null>(null)

  const invite = useQuery({
    queryKey: ['public', 'slot', token],
    queryFn: async () => (await anonymousClient.get<InviteSummary>(`/public/interview-slot/${token}/`)).data,
    retry: false,
    enabled: Boolean(token),
  })

  const select = useMutation({
    mutationFn: async (option: SlotOption) =>
      (await anonymousClient.post(`/public/interview-slot/${token}/`, option)).data,
    onError: (err: unknown) => {
      if (axios.isAxiosError(err) && err.response) {
        const data = err.response.data as { error?: { details?: { slot?: string[] } } }
        setError(data.error?.details?.slot?.[0] ?? 'That time could not be recorded. Please try another.')
      } else {
        setError('Could not reach the server. Please check your connection and try again.')
      }
    },
  })

  const data = invite.data

  return (
    <div className="min-h-screen bg-canvas px-4 py-10">
      <div className="mx-auto w-full max-w-xl">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand text-base font-bold text-white">
            H
          </span>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Interview</p>
            <h1 className="text-lg font-semibold tracking-tight text-ink">
              {data ? `${data.round_name} — ${data.job_title}` : 'Choose your interview time'}
            </h1>
          </div>
        </div>

        {invite.isLoading && <LoadingBlock label="Loading your invitation" />}

        {invite.isError && (
          <Banner tone="danger" title="This invitation link is not valid">
            The link may have been copied incompletely, or the invitation may have been withdrawn.
            Please contact the recruitment team.
          </Banner>
        )}

        {data && select.data != null && (
          <div className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-card">
            <Banner tone="success" title="Time received">
              Thank you, {data.candidate_name}. Our team will confirm your{' '}
              <strong>{data.round_name}</strong> and send you the final schedule
              {' '}— including the meeting details — by email.
            </Banner>
          </div>
        )}

        {data && select.data == null && data.status === 'selected' && (
          <Banner tone="info" title="A time has already been chosen">
            {data.selected ? label(data.selected) : ''} — our team will confirm it by email. If you
            need a different time, reply to the invitation email.
          </Banner>
        )}

        {data && select.data == null && data.status === 'confirmed' && (
          <Banner tone="success" title="Your interview is confirmed">
            The schedule and meeting details were sent to your email.
          </Banner>
        )}

        {data && select.data == null && data.open && (
          <form
            noValidate
            className="space-y-5 rounded-2xl border border-line bg-surface p-6 shadow-card"
            onSubmit={(event) => {
              event.preventDefault()
              setError(null)
              if (!choice) {
                setError('Please choose one of the times.')
                return
              }
              select.mutate(choice)
            }}
          >
            <p className="text-sm text-ink-muted">
              Dear {data.candidate_name}, please choose the time that suits you best for your{' '}
              <strong>{data.round_name}</strong>. Our team will confirm it and send you the final
              schedule.
            </p>
            {error && (
              <Banner tone="danger" title="Not recorded">
                {error}
              </Banner>
            )}
            <fieldset className="space-y-2">
              <legend className="sr-only">Available times</legend>
              {data.options.map((option) => (
                <label
                  key={option.start}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-line px-3 py-2.5 text-sm text-ink hover:border-brand"
                >
                  <input
                    type="radio"
                    name="slot"
                    className="h-4 w-4 text-brand"
                    checked={choice?.start === option.start}
                    onChange={() => setChoice(option)}
                  />
                  {label(option)}
                </label>
              ))}
              {data.options.length === 0 && (
                <Banner tone="warning" title="No times are currently available">
                  Please contact the recruitment team by replying to the invitation email.
                </Banner>
              )}
            </fieldset>
            <Button type="submit" loading={select.isPending} disabled={data.options.length === 0}>
              Confirm my choice
            </Button>
          </form>
        )}

        {data && select.data == null && !data.open && !['selected', 'confirmed'].includes(data.status) && (
          <Banner tone="warning" title="This booking link has expired or is no longer active">
            If your interview was rescheduled, please use the newest link from your most recent
            email — earlier links stop working. Otherwise, contact the recruitment team by
            replying to the invitation email.
          </Banner>
        )}
      </div>
    </div>
  )
}
