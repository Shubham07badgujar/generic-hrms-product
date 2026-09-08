/**
 * Status badges.
 *
 * The tone for every domain status is decided HERE, once. A screen never picks
 * a colour for a status — it passes the status and gets the agreed tone, so
 * "rejected" is the same red on the pipeline board, the candidate profile and
 * the dashboard queue.
 */

import clsx from 'clsx'
import type { ReactNode } from 'react'
import type {
  ApplicationStatus,
  Decision,
  InterviewStatus,
  JobStatus,
  OfferStatus,
  Recommendation,
  StageKind,
  ImportRowStatus,
} from '@/lib/types'
import { humanize } from '@/lib/format'

export type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'

const TONES: Record<Tone, string> = {
  neutral: 'bg-canvas text-ink-muted border-line',
  brand: 'bg-brand-soft text-brand-ink border-brand/20',
  success: 'bg-success-soft text-success-ink border-success/20',
  warning: 'bg-warning-soft text-warning-ink border-warning/25',
  danger: 'bg-danger-soft text-danger-ink border-danger/20',
  info: 'bg-info-soft text-info-ink border-info/20',
}

export function Badge({
  tone = 'neutral',
  children,
  className,
  dot,
  title,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
  dot?: boolean
  /** Full text for truncated badges — long workflow stage names hover-reveal. */
  title?: string
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5',
        'text-2xs font-medium uppercase tracking-wide',
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden />}
      {children}
    </span>
  )
}

const APPLICATION_TONES: Record<ApplicationStatus, Tone> = {
  active: 'info',
  selected: 'success',
  offer_sent: 'brand',
  offer_accepted: 'success',
  offer_declined: 'warning',
  hired: 'success',
  rejected: 'danger',
  withdrawn: 'neutral',
}

const APPLICATION_LABELS: Record<ApplicationStatus, string> = {
  active: 'In progress',
  selected: 'Selected',
  offer_sent: 'Offer sent',
  offer_accepted: 'Offer accepted',
  offer_declined: 'Offer declined',
  hired: 'Hired',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
}

export function ApplicationStatusBadge({ status }: { status: ApplicationStatus }) {
  return (
    <Badge tone={APPLICATION_TONES[status] ?? 'neutral'} dot>
      {APPLICATION_LABELS[status] ?? humanize(status)}
    </Badge>
  )
}

const JOB_TONES: Record<JobStatus, Tone> = {
  draft: 'neutral',
  published: 'success',
  on_hold: 'warning',
  closed: 'neutral',
  filled: 'brand',
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <Badge tone={JOB_TONES[status] ?? 'neutral'}>{humanize(status)}</Badge>
}

const INTERVIEW_TONES: Record<InterviewStatus, Tone> = {
  scheduled: 'info',
  rescheduled: 'warning',
  completed: 'success',
  cancelled: 'neutral',
  no_show: 'danger',
}

export function InterviewStatusBadge({ status }: { status: InterviewStatus }) {
  return <Badge tone={INTERVIEW_TONES[status] ?? 'neutral'}>{humanize(status)}</Badge>
}

const OFFER_TONES: Record<OfferStatus, Tone> = {
  draft: 'neutral',
  sent: 'brand',
  accepted: 'success',
  declined: 'danger',
  withdrawn: 'neutral',
}

export function OfferStatusBadge({ status }: { status: OfferStatus }) {
  return <Badge tone={OFFER_TONES[status] ?? 'neutral'}>{humanize(status)}</Badge>
}

const RECOMMENDATION_TONES: Record<Recommendation, Tone> = {
  strong_hire: 'success',
  hire: 'success',
  hold: 'warning',
  'no hire': 'danger',
}

export function RecommendationBadge({ value }: { value: Recommendation }) {
  return <Badge tone={RECOMMENDATION_TONES[value] ?? 'neutral'}>{humanize(value)}</Badge>
}

/**
 * Decision tone.
 *
 * Note `recommend_reject` is amber, not red: a department recommendation is
 * advisory and routes to HR, and colouring it as a rejection would misrepresent
 * the two-level design on screen.
 */
export const DECISION_TONES: Record<Decision, Tone> = {
  pass: 'success',
  verify: 'success',
  request_info: 'warning',
  screen_out: 'danger',
  recommend_select: 'success',
  recommend_reject: 'warning',
  select: 'success',
  reject: 'danger',
  offer_accepted: 'success',
  offer_declined: 'warning',
  withdraw: 'neutral',
}

export const DECISION_LABELS: Record<Decision, string> = {
  pass: 'Pass',
  verify: 'Verify',
  request_info: 'Request information',
  screen_out: 'Reject application',
  recommend_select: 'Recommend selection',
  recommend_reject: 'Recommend rejection',
  select: 'Select candidate',
  reject: 'Reject candidate',
  offer_accepted: 'Offer accepted',
  offer_declined: 'Offer declined',
  withdraw: 'Withdraw',
}

export function DecisionBadge({ decision }: { decision: Decision }) {
  return (
    <Badge tone={DECISION_TONES[decision] ?? 'neutral'}>
      {DECISION_LABELS[decision] ?? humanize(decision)}
    </Badge>
  )
}

export const STAGE_KIND_LABELS: Record<StageKind, string> = {
  application: 'Application',
  hr_verification: 'HR verification',
  interview: 'Interview',
  department_decision: 'Department decision',
  hr_final_decision: 'HR final decision',
  offer: 'Offer',
  onboarding: 'Onboarding',
  terminal: 'Closed',
}

/**
 * Per-row outcome of a candidate import.
 *
 * Lives here rather than in the import screen for the reason stated at the top
 * of this file: a screen never picks a colour for a domain status. The tones
 * carry meaning — "needs review" is a warning because a human must look at it,
 * while "duplicate" is neutral because nothing went wrong.
 */
const IMPORT_ROW_TONES: Record<ImportRowStatus, Tone> = {
  pending: 'neutral',
  valid: 'info',
  created: 'success',
  matched_updated: 'success',
  matched_skipped: 'neutral',
  duplicate_in_file: 'neutral',
  application_exists: 'neutral',
  needs_review: 'warning',
  invalid: 'danger',
  failed: 'danger',
}

const IMPORT_ROW_LABELS: Record<ImportRowStatus, string> = {
  pending: 'Pending',
  valid: 'Will create',
  created: 'Created',
  matched_updated: 'Updated',
  matched_skipped: 'Already on file',
  duplicate_in_file: 'Duplicate in file',
  application_exists: 'Already applied',
  needs_review: 'Needs review',
  invalid: 'Invalid',
  failed: 'Failed',
}

export function ImportRowBadge({ status }: { status: ImportRowStatus }) {
  return <Badge tone={IMPORT_ROW_TONES[status] ?? 'neutral'}>{IMPORT_ROW_LABELS[status] ?? status}</Badge>
}

export { IMPORT_ROW_LABELS }
