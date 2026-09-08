/**
 * One candidate's application: where they are, how they got there, and what
 * this particular user is allowed to do about it.
 *
 * Everything on this page is derived from server data — the stage list from
 * the workflow, the available actions from the stage's `allowed_decisions`
 * intersected with the permission snapshot, and the history from the
 * `ApplicationEvent` stream. No branch anywhere reads the job title.
 */

import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  useApplication,
  useApplicationHistory,
  useCancelInterview,
  useCandidate,
  useConfigureSlotInvite,
  useInterviews,
  useJob,
  useRejectInterviewTime,
  useRetryCalendarSync,
  useWorkflow,
} from '@/lib/queries'
import { useToast } from '@/components/ui/Toast'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Modal } from '@/components/ui/Modal'
import { TextArea, TextInput } from '@/components/ui/Field'
import { CandidateEmailHistory } from './CandidateEmailHistory'
import { CandidateFormDetails } from './CandidateFormDetails'
import { Card, CardHeader, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import { ErrorState, LoadingBlock, EmptyState } from '@/components/ui/States'
import {
  ApplicationStatusBadge,
  Badge,
  DecisionBadge,
  InterviewStatusBadge,
  RecommendationBadge,
  STAGE_KIND_LABELS,
} from '@/components/ui/Badge'
import { Avatar, Banner, Tabs, TabPanel, Timeline, type TimelineEntry } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { WorkflowStepper } from './WorkflowStepper'
import { StageActions } from './StageActions'
import { OverrideAction } from './OverrideDialog'
import { ScheduleInterviewDrawer } from './ScheduleInterview'
import { FeedbackDrawer } from './FeedbackForm'
import { OfferPanel } from './OfferPanel'
import { ConvertPanel } from './ConvertPanel'
import { useAuth, usePermissions } from '@/app/AuthProvider'
import { ApiError, downloadFile } from '@/lib/api'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { formatDate, formatDateTime, humanize } from '@/lib/format'
import { roleLabel } from '@/app/AppShell'
import type { ApplicationEvent, Candidate, Interview } from '@/lib/types'

const EVENT_TONES: Record<string, TimelineEntry['tone']> = {
  applied: 'neutral',
  stage_changed: 'info',
  verified: 'success',
  info_requested: 'warning',
  interview_scheduled: 'info',
  interview_completed: 'info',
  feedback_submitted: 'brand',
  recommendation: 'warning',
  hr_decision: 'success',
  rejected: 'danger',
  override: 'danger',
  offer_created: 'brand',
  offer_sent: 'brand',
  offer_responded: 'success',
  converted: 'success',
}

const EVENT_TITLES: Record<string, string> = {
  applied: 'Applied',
  stage_changed: 'Stage advanced',
  verified: 'Verified by HR',
  info_requested: 'More information requested',
  interview_scheduled: 'Interview scheduled',
  interview_completed: 'Interview completed',
  feedback_submitted: 'Interview feedback submitted',
  recommendation: 'Department recommendation',
  hr_decision: 'HR Head decision',
  rejected: 'Candidate rejected',
  override: 'Administrative override',
  offer_created: 'Offer created',
  offer_sent: 'Offer sent',
  offer_responded: 'Offer response recorded',
  converted: 'Converted to employee',
}

function eventEntry(event: ApplicationEvent): TimelineEntry {
  const movement =
    event.from_stage_name && event.to_stage_name
      ? `${event.from_stage_name} → ${event.to_stage_name}`
      : (event.to_stage_name ?? undefined)

  return {
    id: event.id,
    tone: EVENT_TONES[event.kind] ?? 'neutral',
    title: EVENT_TITLES[event.kind] ?? humanize(event.kind),
    timestamp: formatDateTime(event.created_at),
    meta: [event.actor_label, movement].filter(Boolean).join(' · ') || undefined,
    body: event.note ? <p className="whitespace-pre-wrap">{event.note}</p> : undefined,
  }
}

/** Profile keys already surfaced by a named card slot — not repeated below. */
const GLANCE_HANDLED = new Set(['city', 'qualification', 'course', 'current_salary', 'resume_link'])

/** A long free-text cell (skills lists, mostly) clipped for the card. */
function clip(value: string): ReactNode {
  return value.length > 180 ? <span title={value}>{value.slice(0, 180)}…</span> : value
}

/**
 * EVERYTHING the candidate record holds, at a glance: the six fixed slots,
 * then the rest of what their source provided (experience, employer, notice,
 * and every remaining profile field), so an imported candidate's sheet data
 * is all visible without opening their profile page.
 */
function candidateGlanceItems(data: Candidate): Array<{ label: string; value: ReactNode }> {
  const profile = data.profile ?? {}
  const items: Array<{ label: string; value: ReactNode }> = [
    {
      label: 'Email',
      value: data.email ? (
        <a href={`mailto:${data.email}`} className="break-all text-brand hover:underline">
          {data.email}
        </a>
      ) : ('—'),
    },
    { label: 'Mobile', value: data.phone || '—' },
    { label: 'City', value: String(profile.city ?? '—') },
    { label: 'Education', value: String(profile.qualification ?? profile.course ?? '—') },
    {
      label: 'Expected salary',
      // Imports from phone-first platforms often carry only the CURRENT
      // salary — better shown and labelled than a dash.
      value: data.expected_ctc
        ? data.expected_ctc
        : profile.current_salary
          ? `${String(profile.current_salary)} (current)`
          : '—',
    },
    {
      label: 'Resume',
      value: data.has_resume ? (
        <Button
          size="sm"
          className="max-w-full"
          title={data.resume_name || 'resume.pdf'}
          onClick={() =>
            void downloadFile(
              `/candidates/${data.id}/resume/`,
              data.resume_name || 'resume.pdf',
            ).catch(() => undefined)
          }
        >
          <span className="truncate">Download</span>
        </Button>
      ) : typeof profile.resume_link === 'string' && profile.resume_link.startsWith('http') ? (
        <a
          href={profile.resume_link}
          target="_blank"
          rel="noreferrer"
          className="text-brand hover:underline"
        >
          Open resume link
        </a>
      ) : ('—'),
    },
  ]

  if (data.total_experience_years) {
    items.push({ label: 'Experience', value: `${data.total_experience_years} years` })
  }
  if (data.current_employer) {
    items.push({ label: 'Current employer', value: data.current_employer })
  }
  if (data.notice_period_days !== null && data.notice_period_days !== undefined) {
    items.push({
      label: 'Notice period',
      value: data.notice_period_days === 0 ? 'Immediate' : `${data.notice_period_days} days`,
    })
  }
  for (const [key, value] of Object.entries(profile)) {
    if (GLANCE_HANDLED.has(key) || value === '' || value === null || value === undefined) continue
    items.push({ label: humanize(key), value: clip(String(value)) })
  }
  return items
}

export function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>()
  const permissions = usePermissions()

  const { user } = useAuth()
  const application = useApplication(id)
  const history = useApplicationHistory(id)
  const job = useJob(application.data?.job_opening)
  const workflow = useWorkflow(job.data?.workflow)
  const interviews = useInterviews({ application: id })
  // The SAME record the Candidates page shows — not a copy of it on the
  // application — so the two routes cannot disagree.
  const candidate = useCandidate(application.data?.candidate)

  const [tab, setTab] = useState('overview')
  const [configuringSlots, setConfiguringSlots] = useState(false)
  const [scheduling, setScheduling] = useState(false)
  const [prefillAt, setPrefillAt] = useState<string | undefined>(undefined)
  const [feedbackFor, setFeedbackFor] = useState<Interview | null>(null)
  const [cancelling, setCancelling] = useState<Interview | null>(null)
  const [rejectingTime, setRejectingTime] = useState<Interview | null>(null)

  if (application.isLoading) return <LoadingBlock label="Loading application" />
  if (application.isError) return <ErrorState error={application.error} />
  if (!application.data) return <EmptyState title="Application not found" />

  const record = application.data
  const stages = workflow.data?.stages ?? []
  const currentStage = stages.find((stage) => stage.id === record.current_stage)
  const closed = record.status !== 'active'
  const interviewRows = interviews.data?.data ?? []

  const canSchedule =
    permissions.can(RESOURCE.INTERVIEW, ACTION.CREATE) &&
    Boolean(currentStage?.requires_interview) &&
    !closed

  // The signed-in interviewer's own unfinished round, surfaced as a button on
  // the stage card so recording feedback never requires leaving this page.
  const myPendingFeedback = interviewRows.find(
    (iv) =>
      iv.stage === record.current_stage &&
      iv.interviewer === user?.employee_id &&
      !iv.feedback_submitted &&
      iv.status !== 'cancelled',
  )

  function refetchAll() {
    void application.refetch()
    void history.refetch()
    void interviews.refetch()
  }

  return (
    <>
      <PageHeader
        breadcrumbs={
          <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
            <Link to="/recruitment/pipeline" className="hover:text-ink hover:underline">
              Pipeline
            </Link>
            <span className="px-1.5">/</span>
            <span className="text-ink-muted">{record.candidate_name}</span>
          </nav>
        }
        title={
          <span className="flex min-w-0 items-center gap-3">
            <Avatar name={record.candidate_name} size="md" />
            <span className="truncate" title={record.candidate_name}>{record.candidate_name}</span>
          </span>
        }
        description={`${record.job_title} · ${record.department_name}`}
        meta={
          <>
            <ApplicationStatusBadge status={record.status} />
            <Badge tone="info" className="max-w-[260px]" title={record.stage_name}>
              <span className="truncate">{record.stage_name}</span>
            </Badge>
            {record.is_verified ? (
              <Badge tone="success">HR verified</Badge>
            ) : (
              <Badge tone="warning">Not yet verified</Badge>
            )}
            <span className="text-xs text-ink-subtle">Applied {formatDate(record.applied_at)}</span>
          </>
        }
        actions={
          <>
            <Link to={`/recruitment/candidates/${record.candidate}`}>
              <Button>Candidate profile</Button>
            </Link>
            <OverrideAction application={record} onDone={refetchAll} />
          </>
        }
      />

      {!record.is_verified && !closed && (
        <Banner tone="warning" title="Department stages are closed until HR verifies">
          The workflow's transition table will not admit this candidate to an interview stage before
          HR verification. This is enforced by the engine, not only by this screen.
        </Banner>
      )}

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: 'overview', label: 'Overview' },
          { id: 'interviews', label: 'Interviews', count: interviewRows.length },
          { id: 'emails', label: 'Emails' },
          { id: 'history', label: 'History', count: history.data?.events.length },
        ]}
      />

      <TabPanel id="emails" active={tab}>
        <CandidateEmailHistory
          applicationId={record.id}
          mayRetry={permissions.can(RESOURCE.APPLICATION, ACTION.EDIT)}
        />
      </TabPanel>

      <TabPanel id="overview" active={tab}>
        {candidate.data && (
          <Card className="mb-5">
            <DescriptionList columns={3} items={candidateGlanceItems(candidate.data)} />
          </Card>
        )}
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="space-y-5 lg:col-span-2">
            <Card className="space-y-4">
              <CardHeader
                title="Current stage"
                description={
                  currentStage
                    ? `${STAGE_KIND_LABELS[currentStage.kind] ?? currentStage.kind}${
                        currentStage.responsible_role_code
                          ? ` · ${roleLabel(currentStage.responsible_role_code)}`
                          : ''
                      }`
                    : undefined
                }
                action={
                  <span className="flex flex-wrap gap-2">
                    {myPendingFeedback && (
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => setFeedbackFor(myPendingFeedback)}
                      >
                        Submit feedback
                      </Button>
                    )}
                    {canSchedule && (
                      <Button
                        variant={myPendingFeedback ? 'secondary' : 'primary'}
                        size="sm"
                        onClick={() => setScheduling(true)}
                      >
                        Schedule interview
                      </Button>
                    )}
                  </span>
                }
              />

              {currentStage?.requires_feedback && !closed && !myPendingFeedback && (
                <Banner tone="info">
                  This stage cannot be decided until an interview has taken place and feedback has
                  been submitted. The engine checks both before accepting a decision.
                </Banner>
              )}

              <StageActions application={record} onDone={refetchAll} />
            </Card>

            {canSchedule && currentStage?.kind === 'interview' && !record.slot_invite && (
              <Card className="space-y-3">
                <CardHeader
                  title="Interview slot selection"
                  description="The candidate has NOT been sent a booking link for this round. Configure the available times to email it — they will only ever see slots that are still free."
                  action={
                    <Button variant="primary" size="sm" onClick={() => setConfiguringSlots(true)}>
                      Set available times
                    </Button>
                  }
                />
              </Card>
            )}

            {record.slot_invite && (
              <Card className="space-y-3">
                <CardHeader
                  title="Interview slot selection"
                  description={
                    record.slot_invite.status === 'selected'
                      ? 'The candidate has chosen a time. Confirm it by scheduling the interview — the final schedule and meeting link go out automatically.'
                      : 'The candidate has been emailed a link to choose an interview time.'
                  }
                />
                {record.slot_invite.status === 'selected' && record.slot_invite.selected_slot ? (
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm font-medium text-ink">
                      {new Date(record.slot_invite.selected_slot.start).toLocaleString(undefined, {
                        weekday: 'long', day: 'numeric', month: 'long',
                        hour: 'numeric', minute: '2-digit',
                      })}
                      {' – '}
                      {new Date(record.slot_invite.selected_slot.end).toLocaleTimeString(undefined, {
                        hour: 'numeric', minute: '2-digit',
                      })}
                    </p>
                    {canSchedule && (
                      <Button
                        variant="primary"
                        onClick={() => {
                          // Pre-fill the drawer with the candidate's chosen start;
                          // the ordinary conflict check still runs.
                          const start = new Date(record.slot_invite!.selected_slot!.start)
                          const pad = (n: number) => String(n).padStart(2, '0')
                          setPrefillAt(
                            `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}`,
                          )
                          setScheduling(true)
                        }}
                      >
                        Confirm & schedule
                      </Button>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-ink-muted">
                    Waiting for the candidate — the invitation expires{' '}
                    {new Date(record.slot_invite.expires_at).toLocaleDateString()}. You can still
                    schedule directly at any time.
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  {record.slot_invite.invite_count > 1 && (
                    <Badge tone="warning">
                      Rebooked ×{record.slot_invite.invite_count - 1}
                    </Badge>
                  )}
                  <CopyBookingLink url={record.slot_invite.selection_url} />
                  {canSchedule && (
                    <Button size="sm" variant="ghost" onClick={() => setConfiguringSlots(true)}>
                      Change the offered times
                    </Button>
                  )}
                </div>
              </Card>
            )}

            <OfferPanel application={record} onChanged={refetchAll} />
            <ConvertPanel application={record} onConverted={refetchAll} />

            {history.data?.rejection && (
              <Card className="space-y-4">
                <CardHeader title="Rejection record" />
                <Banner
                  tone={history.data.rejection.is_overridden ? 'warning' : 'danger'}
                  title={
                    history.data.rejection.is_overridden
                      ? 'This rejection was later set aside by an administrator'
                      : 'Rejected by HR Head'
                  }
                >
                  <p className="whitespace-pre-wrap">{history.data.rejection.reason}</p>
                </Banner>
                <DescriptionList
                  columns={2}
                  items={[
                    { label: 'Decided by', value: history.data.rejection.rejected_by_email },
                    { label: 'When', value: formatDateTime(history.data.rejection.rejected_at) },
                    { label: 'At stage', value: history.data.rejection.stage_name },
                    {
                      label: 'Status',
                      value: history.data.rejection.is_overridden ? (
                        <Badge tone="warning">Overridden</Badge>
                      ) : (
                        <Badge tone="danger">Final</Badge>
                      ),
                    },
                  ]}
                />
              </Card>
            )}

            {(history.data?.overrides.length ?? 0) > 0 && (
              <Card className="space-y-4">
                <CardHeader
                  title="Administrative overrides"
                  description="Recorded separately from HR decisions and written to the audit log."
                />
                <div className="space-y-3">
                  {history.data!.overrides.map((override) => (
                    <div key={override.id} className="rounded-lg border border-line p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-medium text-ink">
                          {humanize(override.previous_status)} → {humanize(override.new_status)}
                        </p>
                        <span className="text-xs text-ink-subtle">
                          {formatDateTime(override.overridden_at)}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-ink-muted">
                        {override.overridden_by_email}
                      </p>
                      {/* The stage move, when there was one. Reopening a
                          rejected candidate returns them to a workable stage,
                          and "back to X" is the part that explains how the
                          application became actionable again. */}
                      {override.new_stage_name &&
                        override.new_stage_name !== override.previous_stage_name && (
                          <p className="mt-1 text-xs text-ink-muted">
                            Stage: {override.previous_stage_name ?? '—'} →{' '}
                            <span className="font-medium text-ink">{override.new_stage_name}</span>
                          </p>
                        )}
                      <p className="mt-2 whitespace-pre-wrap text-sm text-ink-muted">
                        {override.reason}
                      </p>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {(history.data?.decisions.length ?? 0) > 0 && (
              <Card className="space-y-4">
                <CardHeader title="Decisions recorded" />
                <div className="space-y-3">
                  {history.data!.decisions.map((decision, index) => (
                    <div key={`${decision.stage}-${index}`} className="rounded-lg border border-line p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-ink">{decision.stage}</span>
                          <DecisionBadge decision={decision.decision} />
                        </div>
                        <span className="text-xs text-ink-subtle">{formatDateTime(decision.at)}</span>
                      </div>
                      <p className="mt-1 text-xs text-ink-muted">{decision.by}</p>
                      {decision.rationale && (
                        <p className="mt-2 whitespace-pre-wrap text-sm text-ink-muted">
                          {decision.rationale}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>

          <div className="space-y-5">
            <Card className="space-y-4">
              <CardHeader
                title="Pipeline"
                description={workflow.data?.name}
              />
              {workflow.isLoading ? (
                <LoadingBlock label="Loading the workflow" />
              ) : workflow.data ? (
                <WorkflowStepper
                  workflow={workflow.data}
                  currentStageId={record.current_stage}
                  closed={closed}
                  hired={record.status === 'hired'}
                />
              ) : (
                <EmptyState compact title="Workflow unavailable" />
              )}
            </Card>

            {candidate.data ? (
              <CandidateFormDetails
                candidate={candidate.data}
                description="What the candidate submitted, identical to their profile page."
              />
            ) : (
              <Card>
                <CardHeader title="Candidate information" />
                <LoadingBlock />
              </Card>
            )}

            <Card className="space-y-4">
              <CardHeader title="Application" />
              <DescriptionList
                columns={1}
                items={[
                  // The same reference the candidate is emailed, so a call
                  // quoting "APP-1A2B3C4D" lands on the right record.
                  { label: 'Reference', value: <span className="font-mono text-xs">APP-{record.id.slice(0, 8).toUpperCase()}</span> },
                  { label: 'Job', value: <Link className="text-brand hover:underline" to={`/recruitment/jobs/${record.job_opening}`}>{record.job_title}</Link> },
                  { label: 'Department', value: record.department_name },
                  { label: 'Workflow', value: workflow.data?.name ?? '—' },
                  { label: 'Applied', value: formatDateTime(record.applied_at) },
                  { label: 'HR verification', value: record.is_verified ? 'Verified' : 'Pending' },
                  { label: 'Status', value: <ApplicationStatusBadge status={record.status} /> },
                ]}
              />
              {Object.keys(record.form_answers ?? {}).filter((key) => !key.startsWith('_')).length > 0 && (
                <div className="space-y-1 border-t border-line pt-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                    Application form answers
                  </p>
                  <DescriptionList
                    columns={1}
                    items={Object.entries(record.form_answers)
                      .filter(([key]) => !key.startsWith('_'))
                      .map(([key, value]) => ({
                        label: humanize(key),
                        value: Array.isArray(value) ? value.join(', ') : String(value),
                      }))}
                  />
                </div>
              )}
              {record.form_answers?._source && (
                <p className="text-xs text-ink-subtle">
                  Received via {record.form_answers._source === 'google_forms' ? 'Google Form' : 'the application form'}.
                </p>
              )}
            </Card>
          </div>
        </div>
      </TabPanel>

      <TabPanel id="interviews" active={tab}>
        <Section
          title="Interviews"
          action={
            canSchedule && (
              <Button variant="primary" size="sm" onClick={() => setScheduling(true)}>
                Schedule interview
              </Button>
            )
          }
        >
          {interviews.isLoading ? (
            <LoadingBlock />
          ) : interviewRows.length === 0 ? (
            <Card>
              <EmptyState
                title="No interviews scheduled"
                description="Interviews appear here once a stage requiring one has been booked."
              />
            </Card>
          ) : (
            <div className="space-y-3">
              {interviewRows.map((interview) => {
                const stage = stages.find((candidate) => candidate.id === interview.stage)
                const canGiveFeedback =
                  !interview.feedback_submitted &&
                  permissions.can(RESOURCE.INTERVIEW_FEEDBACK, ACTION.CREATE)

                return (
                  <Card key={interview.id} className="flex flex-wrap items-center gap-4">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="min-w-0 break-words text-sm font-medium text-ink">{interview.stage_name}</p>
                        <InterviewStatusBadge status={interview.status} />
                        {interview.feedback_submitted && <Badge tone="success">Feedback in</Badge>}
                      </div>
                      <p className="text-xs text-ink-muted">
                        {interview.interviewer_name} · {formatDateTime(interview.scheduled_at)} ·{' '}
                        {interview.duration_minutes} min · {humanize(interview.mode)}
                      </p>
                      {interview.location_or_link && (
                        <p className="truncate text-xs text-ink-subtle">
                          {interview.location_or_link.startsWith('http') ? (
                            <a
                              href={interview.location_or_link}
                              target="_blank"
                              rel="noreferrer"
                              className="text-brand hover:underline"
                            >
                              {interview.location_or_link}
                            </a>
                          ) : (
                            interview.location_or_link
                          )}
                        </p>
                      )}
                      {interview.calendar_sync_status && (
                        <p className="flex items-center gap-2 text-xs">
                          {interview.calendar_sync_status === 'synced' && (
                            <Badge tone="success">Calendar invite sent</Badge>
                          )}
                          {interview.calendar_sync_status === 'cancelled' && (
                            <Badge tone="neutral">Calendar event cancelled</Badge>
                          )}
                          {interview.calendar_sync_status === 'failed' && (
                            <>
                              <Badge tone="danger">Calendar sync failed</Badge>
                              {permissions.can(RESOURCE.INTERVIEW, ACTION.EDIT) && (
                                <RetryCalendarButton interview={interview} onDone={refetchAll} />
                              )}
                            </>
                          )}
                        </p>
                      )}
                    </div>
                    {canGiveFeedback && (
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => setFeedbackFor(interview)}
                        // Only the assigned interviewer may submit; the API
                        // enforces it and returns a clear message otherwise.
                      >
                        Submit feedback
                      </Button>
                    )}
                    {stage?.feedback_form === null && (
                      <Badge tone="warning">No form configured</Badge>
                    )}
                    {(interview.status === 'scheduled' || interview.status === 'rescheduled') &&
                      permissions.can(RESOURCE.INTERVIEW, ACTION.EDIT) && (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setRejectingTime(interview)}
                            // The time, not the candidate: cancels this booking
                            // and automatically re-invites them to pick again.
                          >
                            Reject time
                          </Button>
                          <Button size="sm" variant="danger-soft" onClick={() => setCancelling(interview)}>
                            Cancel
                          </Button>
                        </>
                      )}
                  </Card>
                )
              })}
            </div>
          )}
        </Section>

        {(history.data?.interviews.length ?? 0) > 0 && (
          <Section title="Recorded assessments" className="pt-6">
            <div className="space-y-3">
              {history.data!.interviews
                .filter((entry) => entry.recommendation)
                .map((entry, index) => (
                  <Card key={index} className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-ink">{entry.stage}</span>
                        {entry.recommendation && (
                          <RecommendationBadge value={entry.recommendation} />
                        )}
                        {entry.rating !== null && (
                          <span className="tabular text-xs text-ink-muted">{entry.rating}/5</span>
                        )}
                      </div>
                      <span className="text-xs text-ink-subtle">{entry.interviewer}</span>
                    </div>
                    {entry.strengths && (
                      <p className="text-sm text-ink-muted">
                        <span className="font-medium text-ink">Strengths: </span>
                        {entry.strengths}
                      </p>
                    )}
                    {entry.concerns && (
                      <p className="text-sm text-ink-muted">
                        <span className="font-medium text-ink">Concerns: </span>
                        {entry.concerns}
                      </p>
                    )}
                  </Card>
                ))}
            </div>
          </Section>
        )}
      </TabPanel>

      <TabPanel id="history" active={tab}>
        <Card>
          <CardHeader
            title="Complete history"
            description="Every stage movement, interview, decision and override, append-only."
          />
          <div className="pt-4">
            {history.isLoading ? (
              <LoadingBlock />
            ) : history.isError ? (
              <ErrorState error={history.error} onRetry={() => void history.refetch()} />
            ) : (history.data?.events.length ?? 0) === 0 ? (
              <EmptyState title="No events recorded yet" />
            ) : (
              <Timeline entries={(history.data?.events ?? []).map(eventEntry)} />
            )}
          </div>
        </Card>
      </TabPanel>

      {currentStage && (
        <ScheduleInterviewDrawer
          open={scheduling}
          onClose={() => {
            setScheduling(false)
            setPrefillAt(undefined)
          }}
          application={record}
          stage={currentStage}
          onScheduled={refetchAll}
          initialScheduledAt={prefillAt}
        />
      )}

      {configuringSlots && (
        <ConfigureSlotsDialog
          applicationId={record.id}
          candidateName={record.candidate_name}
          stageName={record.stage_name}
          hasOpenInvite={Boolean(record.slot_invite)}
          onClose={() => setConfiguringSlots(false)}
          onDone={refetchAll}
        />
      )}

      {cancelling && (
        <CancelInterviewDialog
          interview={cancelling}
          onClose={() => setCancelling(null)}
          onCancelled={refetchAll}
        />
      )}

      {rejectingTime && (
        <RejectTimeDialog
          interview={rejectingTime}
          onClose={() => setRejectingTime(null)}
          onDone={refetchAll}
        />
      )}

      {feedbackFor && (
        <FeedbackDrawer
          open
          onClose={() => setFeedbackFor(null)}
          interview={feedbackFor}
          formId={stages.find((stage) => stage.id === feedbackFor.stage)?.feedback_form ?? null}
          onSubmitted={refetchAll}
        />
      )}
    </>
  )
}


/**
 * Cancelling frees the interviewer's slot and tells the candidate. It is not
 * a decision about the candidate — the application stays where it is.
 */
/**
 * HR sets the round's available windows; submitting emails the candidate the
 * booking link. Reconfiguring an open invite kills its link and sends a fresh
 * one, which the dialog warns about.
 */
/** A Date as the value/min a datetime-local input takes, in local time. */
function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

function ConfigureSlotsDialog({
  applicationId,
  candidateName,
  stageName,
  hasOpenInvite,
  onClose,
  onDone,
}: {
  applicationId: string
  candidateName: string
  stageName: string
  hasOpenInvite: boolean
  onClose: () => void
  onDone: () => void
}) {
  const toast = useToast()
  const configure = useConfigureSlotInvite(applicationId)
  const [windows, setWindows] = useState<{ start: string; minutes: string }[]>([
    { start: '', minutes: '60' },
  ])
  const [errors, setErrors] = useState<Record<string, string>>({})

  const filled = windows.filter((w) => w.start)

  function buildOptions() {
    return filled.map((w) => {
      const start = new Date(w.start)
      const end = new Date(start.getTime() + Number(w.minutes || 60) * 60000)
      return { start: start.toISOString(), end: end.toISOString() }
    })
  }

  /** The server's already-passed refusal, caught before the round trip. */
  function passedWindowError(): string | null {
    const now = Date.now()
    const passed = filled.find((w) => new Date(w.start).getTime() <= now)
    if (!passed) return null
    const at = new Date(passed.start)
    return (
      `The ${at.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })} ` +
      'window has already passed. Same-day slots are fine — just pick a time later than now.'
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      busy={configure.isPending}
      title="Set the available interview times"
      description={`${candidateName} — ${stageName}`}
      footer={
        <>
          <Button onClick={onClose} disabled={configure.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={configure.isPending}
            disabled={filled.length === 0}
            onClick={() => {
              setErrors({})
              const passed = passedWindowError()
              if (passed) {
                setErrors({ options: passed })
                return
              }
              configure.mutate(
                { options: buildOptions() },
                {
                  onSuccess: () => {
                    toast.success(
                      'Booking link sent',
                      'The candidate has been emailed the slot-selection link.',
                    )
                    onClose()
                    onDone()
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not send the booking link')
                  },
                },
              )
            }}
          >
            Send booking link
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Banner tone="info" title="The candidate sees only what is still free">
          These windows are offered on the booking page minus anything another candidate of the
          same round has already taken, so a time can never be double-booked.
          {hasOpenInvite &&
            ' Sending new times cancels the current booking link and emails a fresh one.'}
        </Banner>

        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">Available windows</p>
          {windows.map((window, index) => (
            <div key={index} className="flex items-center gap-2">
              <TextInput
                label=""
                type="datetime-local"
                min={toLocalInputValue(new Date())}
                value={window.start}
                onChange={(event) =>
                  setWindows((rows) =>
                    rows.map((row, i) => (i === index ? { ...row, start: event.target.value } : row)),
                  )
                }
              />
              <select
                className="rounded-lg border border-line bg-surface px-2 py-2 text-sm"
                value={window.minutes}
                onChange={(event) =>
                  setWindows((rows) =>
                    rows.map((row, i) =>
                      i === index ? { ...row, minutes: event.target.value } : row,
                    ),
                  )
                }
              >
                <option value="30">30 min</option>
                <option value="45">45 min</option>
                <option value="60">1 hour</option>
                <option value="120">2 hours</option>
              </select>
              {windows.length > 1 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setWindows((rows) => rows.filter((_, i) => i !== index))}
                >
                  Remove
                </Button>
              )}
            </div>
          ))}
          {windows.length < 10 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setWindows((rows) => [...rows, { start: '', minutes: '60' }])}
            >
              + Add a time window
            </Button>
          )}
          {errors.options && <p className="text-xs text-danger">{errors.options}</p>}
          {errors.stage && <p className="text-xs text-danger">{errors.stage}</p>}
          {errors.candidate && <p className="text-xs text-danger">{errors.candidate}</p>}
        </div>
      </div>
    </Modal>
  )
}

/** Copy the candidate's live booking link — to re-send by hand if ever needed. */
function CopyBookingLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        void navigator.clipboard.writeText(url).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 2000)
        })
      }}
    >
      {copied ? 'Copied!' : 'Copy booking link'}
    </Button>
  )
}

/** One-click retry after a Google outage; the server records the outcome. */
function RetryCalendarButton({
  interview,
  onDone,
}: {
  interview: Interview
  onDone: () => void
}) {
  const toast = useToast()
  const retry = useRetryCalendarSync(interview.id)
  return (
    <Button
      size="sm"
      variant="ghost"
      loading={retry.isPending}
      onClick={() =>
        retry.mutate(undefined, {
          onSuccess: (updated) => {
            if (updated.calendar_sync_status === 'failed') {
              toast.error('Still failing', updated.calendar_error || 'Google refused again.')
            } else {
              toast.success('Calendar synced', 'The invitation has gone out.')
            }
            onDone()
          },
          onError: (error) => toast.fromError(error, 'Could not retry'),
        })
      }
    >
      Retry
    </Button>
  )
}

/**
 * The interviewer's "this time does not work": cancels the booking (and its
 * calendar event), kills the old booking link, and automatically emails the
 * candidate a fresh one — offering the proposed windows below when given,
 * the standard windows otherwise. The application itself does not move.
 */
function RejectTimeDialog({
  interview,
  onClose,
  onDone,
}: {
  interview: Interview
  onClose: () => void
  onDone: () => void
}) {
  const toast = useToast()
  const reject = useRejectInterviewTime(interview.id)
  const [reason, setReason] = useState('')
  const [windows, setWindows] = useState<{ start: string; minutes: string }[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})

  function buildOptions() {
    const rows = windows.filter((w) => w.start)
    if (rows.length === 0) return undefined
    return rows.map((w) => {
      const start = new Date(w.start)
      const end = new Date(start.getTime() + Number(w.minutes || 60) * 60000)
      return { start: start.toISOString(), end: end.toISOString() }
    })
  }

  return (
    <Modal
      open
      onClose={onClose}
      busy={reject.isPending}
      title="Reject this time & rebook the candidate"
      description={`${interview.stage_name} with ${interview.interviewer_name} on ${formatDateTime(interview.scheduled_at)}`}
      footer={
        <>
          <Button onClick={onClose} disabled={reject.isPending}>
            Keep the booking
          </Button>
          <Button
            variant="danger"
            loading={reject.isPending}
            disabled={reason.trim().length < 5}
            onClick={() => {
              setErrors({})
              reject.mutate(
                { reason: reason.trim(), options: buildOptions() },
                {
                  onSuccess: () => {
                    toast.success(
                      'Time rejected',
                      'The candidate has been emailed a new booking link.',
                    )
                    onClose()
                    onDone()
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not reject the time')
                  },
                },
              )
            }}
          >
            Reject time & send new link
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Banner tone="info" title="This rejects the TIME, not the candidate">
          The interview and its calendar invitation are cancelled, the old booking link stops
          working, and the candidate automatically receives a fresh link to choose a new time.
          Their application stays exactly where it is.
        </Banner>

        <TextArea
          label="Reason"
          required
          rows={2}
          placeholder="e.g. Clinical schedule conflict"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          error={errors.reason}
          description="Recorded in the application history and included in the candidate's email."
        />

        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">
            Propose specific times{' '}
            <span className="font-normal text-ink-subtle">
              (optional — leave empty to offer the standard windows)
            </span>
          </p>
          {windows.map((window, index) => (
            <div key={index} className="flex items-center gap-2">
              <TextInput
                label=""
                type="datetime-local"
                min={toLocalInputValue(new Date())}
                value={window.start}
                onChange={(event) =>
                  setWindows((rows) =>
                    rows.map((row, i) => (i === index ? { ...row, start: event.target.value } : row)),
                  )
                }
              />
              <select
                className="rounded-lg border border-line bg-surface px-2 py-2 text-sm"
                value={window.minutes}
                onChange={(event) =>
                  setWindows((rows) =>
                    rows.map((row, i) =>
                      i === index ? { ...row, minutes: event.target.value } : row,
                    ),
                  )
                }
              >
                <option value="30">30 min</option>
                <option value="45">45 min</option>
                <option value="60">1 hour</option>
                <option value="120">2 hours</option>
              </select>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setWindows((rows) => rows.filter((_, i) => i !== index))}
              >
                Remove
              </Button>
            </div>
          ))}
          {windows.length < 6 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setWindows((rows) => [...rows, { start: '', minutes: '60' }])}
            >
              + Add a time window
            </Button>
          )}
          {errors.options && <p className="text-xs text-danger">{errors.options}</p>}
        </div>
      </div>
    </Modal>
  )
}

function CancelInterviewDialog({
  interview,
  onClose,
  onCancelled,
}: {
  interview: Interview
  onClose: () => void
  onCancelled: () => void
}) {
  const toast = useToast()
  const cancel = useCancelInterview(interview.id)
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      loading={cancel.isPending}
      tone="danger"
      title="Cancel this interview"
      description={`${interview.stage_name} with ${interview.interviewer_name} on ${formatDateTime(interview.scheduled_at)}. The candidate will be emailed that it is cancelled; the application stays open at its current stage.`}
      confirmLabel="Cancel interview"
      onConfirm={() =>
        cancel.mutate(
          {},
          {
            onSuccess: () => {
              toast.success('Interview cancelled', 'The candidate has been notified.')
              onClose()
              onCancelled()
            },
            onError: (error) => toast.fromError(error, 'Could not cancel'),
          },
        )
      }
    />
  )
}
