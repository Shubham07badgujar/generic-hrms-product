/**
 * The decision controls for whatever stage an application is currently at.
 *
 * WHAT IS RENDERED COMES FROM THE SERVER. `application.allowed_decisions` is
 * the stage's own `allowed_decisions` column, so the buttons are literally the
 * workflow configuration. Adding `request_info` to a stage in the database
 * makes the button appear; nothing here enumerates stages or job titles.
 *
 * WHICH ROUTE EACH DECISION POSTS TO IS NOT A UI CHOICE. The backend splits
 * the action routes by permission on purpose:
 *
 *   pass / verify / request_info   → /advance/    APPLICATION/EDIT
 *   recommend_select / _reject     → /recommend/  DEPARTMENT_DECISION/RECOMMEND
 *   select                         → /select/     APPLICATION/APPROVE
 *   reject                         → /reject/     APPLICATION/REJECT   (HR Head only)
 *
 * Posting a terminal decision to /advance/ is rejected by the API with a
 * message pointing at the right route, so this mapping is mirrored rather than
 * invented — and a mismatch surfaces as an error rather than a privilege hole.
 */

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Banner } from '@/components/ui/Misc'
import { DECISION_LABELS } from '@/components/ui/Badge'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useAdvanceApplication,
  useRecommendApplication,
  useRejectCandidate,
  useSelectCandidate,
} from '@/lib/queries'
import { ApiError } from '@/lib/api'
import type { Application, Decision } from '@/lib/types'

const ADVISORY: Decision[] = ['recommend_select', 'recommend_reject']

/** Which permission each decision needs, mirroring the backend's route split. */
/**
 * Mirrors `engine._rbac_action_for`: decision first, then the stage KIND. An
 * interview round's progression is the interviewer's verdict, so it is
 * authorised as INTERVIEW_FEEDBACK/CREATE — not APPLICATION/EDIT. Mapping it
 * to the latter here is what showed a Pass button the server then refused.
 */
function permissionFor(decision: Decision, stageKind: string): [string, string] {
  if (decision === 'reject') return [RESOURCE.APPLICATION, ACTION.REJECT]
  if (decision === 'select') return [RESOURCE.APPLICATION, ACTION.APPROVE]
  // At an interview round an advisory decision is the interviewer's own
  // verdict — authorised like their pass. Only at a department-decision stage
  // is it the department's recommendation.
  if (ADVISORY.includes(decision) && stageKind !== 'interview')
    return [RESOURCE.DEPARTMENT_DECISION, ACTION.RECOMMEND]
  if (stageKind === 'interview') return [RESOURCE.INTERVIEW_FEEDBACK, ACTION.CREATE]
  if (stageKind === 'department_decision') return [RESOURCE.DEPARTMENT_DECISION, ACTION.RECOMMEND]
  if (stageKind === 'hr_final_decision') return [RESOURCE.APPLICATION, ACTION.APPROVE]
  if (stageKind === 'offer') return [RESOURCE.OFFER, ACTION.EDIT]
  if (stageKind === 'onboarding') return [RESOURCE.ONBOARDING, ACTION.EDIT]
  return [RESOURCE.APPLICATION, ACTION.EDIT]
}

export function StageActions({
  application,
  onDone,
}: {
  application: Application
  onDone?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()

  const advance = useAdvanceApplication(application.id)
  const recommend = useRecommendApplication(application.id)
  const select = useSelectCandidate(application.id)
  const reject = useRejectCandidate(application.id)

  const [confirming, setConfirming] = useState<Decision | null>(null)
  const [rationaleFor, setRationaleFor] = useState<Decision | null>(null)
  const [screeningOut, setScreeningOut] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [serverError, setServerError] = useState<string | undefined>()

  const closed = application.status !== 'active'
  const decisions = application.allowed_decisions ?? []

  if (closed) {
    return (
      <Banner tone="neutral" title="This application is closed">
        No further stage decisions can be recorded. An administrator can reopen it through the
        audited override, which is recorded separately from any HR decision.
      </Banner>
    )
  }

  if (!decisions.length) {
    return (
      <Banner tone="neutral" title="No decisions configured at this stage">
        This stage does not accept a decision. Movement happens elsewhere in the workflow.
      </Banner>
    )
  }

  // Layer 2 of the engine's authorisation, mirrored here so the panel does
  // not offer a button the server will refuse: the workflow names ONE role
  // for each stage, and only a holder of that role may record its decision.
  // The API remains the authority — this only stops a doomed click.
  const responsible = application.stage_responsible_role
  if (responsible && !permissions.hasRole(responsible)) {
    return (
      <Banner
        tone="info"
        title={`This stage is actioned by ${application.stage_responsible_role_name ?? responsible}`}
      >
        The workflow assigns “{application.stage_name}” to the {application.stage_responsible_role_name ?? responsible} role. Sign in as
        that role to record {decisions.map((d) => d.replace(/_/g, ' ')).join(' / ')} here.
        {permissions.hasRole('admin') && ' As an administrator you can use the audited override instead.'}
      </Banner>
    )
  }

  // An interview round has no decision to record until the interview exists.
  // The server enforces this (and demands feedback too); withholding the
  // buttons keeps the panel honest about what would succeed.
  if (application.stage_kind === 'interview' && !application.stage_interview_scheduled) {
    return (
      <Banner tone="info" title="Schedule this round first">
        Pass / Reject opens once the interview is scheduled: configure the available times,
        let the candidate pick a slot, then confirm the schedule. The decision buttons appear
        here after that.
      </Banner>
    )
  }

  // Scheduled, but not yet judged: the round's verdict IS the interviewer's
  // submitted feedback, so the decision buttons stay closed until it exists.
  // The engine refuses a pass without it either way — this keeps the panel
  // honest about what would succeed.
  if (application.stage_kind === 'interview' && !application.stage_feedback_submitted) {
    return (
      <Banner tone="info" title="Waiting for the interviewer's feedback">
        The candidate can move on only after the assigned interviewer submits the interview
        feedback form for this round. Pass / recommend opens the moment that feedback is in —
        the interviewer records it from this page or from My interviews.
      </Banner>
    )
  }

  // Split by what this user may actually do, so the panel can explain the
  // difference rather than silently showing an empty box.
  const permitted = decisions.filter((decision) => {
    const [resource, action] = permissionFor(decision, application.stage_kind)
    return permissions.can(resource as never, action as never)
  })
  const withheld = decisions.filter((decision) => !permitted.includes(decision))

  const busy =
    advance.isPending || recommend.isPending || select.isPending || reject.isPending

  function handleError(error: unknown) {
    if (error instanceof ApiError) {
      setServerError(error.displayMessage)
      toast.fromError(error, 'Decision not recorded')
    } else {
      toast.fromError(error, 'Decision not recorded')
    }
  }

  function afterSuccess(message: string) {
    setConfirming(null)
    setRationaleFor(null)
    setRejecting(false)
    setServerError(undefined)
    toast.success(message)
    onDone?.()
  }

  function runAdvance(decision: Decision) {
    advance.mutate(
      { decision },
      {
        onSuccess: (result) =>
          afterSuccess(
            result.to_stage ? `Moved to ${result.to_stage}.` : 'Decision recorded.',
          ),
        onError: handleError,
      },
    )
  }

  function runRecommend(decision: Decision, rationale: string) {
    const callbacks = {
      onSuccess: () =>
        afterSuccess(
          decision === 'recommend_reject'
            ? 'Recommendation recorded and routed to HR Head.'
            : 'Recommendation recorded.',
        ),
      onError: handleError,
    }
    // An interviewer's advisory verdict travels the advance route (the
    // engine authorises it as their round's own act); a department
    // recommendation keeps the dedicated recommend route and its gate.
    if (application.stage_kind === 'interview') {
      advance.mutate({ decision, rationale }, callbacks)
    } else {
      recommend.mutate({ decision, rationale }, callbacks)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {permitted.map((decision) => {
          const isTerminalReject = decision === 'reject'
          const isScreenOut = decision === 'screen_out'
          const isAdvisory = ADVISORY.includes(decision)
          return (
            <Button
              key={decision}
              variant={
                isTerminalReject
                  ? 'danger'
                  : decision === 'select'
                    ? 'primary'
                    : decision === 'recommend_reject'
                      ? 'danger-soft'
                      : 'secondary'
              }
              disabled={busy}
              onClick={() => {
                setServerError(undefined)
                if (isTerminalReject) setRejecting(true)
                else if (isScreenOut) setScreeningOut(true)
                else if (isAdvisory) setRationaleFor(decision)
                else setConfirming(decision)
              }}
            >
              {DECISION_LABELS[decision] ?? decision}
            </Button>
          )
        })}
      </div>

      {permitted.length === 0 && (
        <Banner tone="info" title="This stage is not yours to action">
          {withheld.length === 1 && withheld[0] === 'reject'
            ? 'Only the HR Head can record the final decision here.'
            : 'Another role is responsible for the decision at this stage.'}
        </Banner>
      )}

      {/* Ordinary movement: a plain confirmation. `select` is excluded — it has
          its own dialog below, and both matching would open two at once. */}
      <ConfirmDialog
        open={confirming !== null && confirming !== 'select'}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && runAdvance(confirming)}
        loading={advance.isPending}
        title={confirming ? (DECISION_LABELS[confirming] ?? 'Record decision') : ''}
        description={`${application.candidate_name} — ${application.stage_name}`}
        confirmLabel="Record decision"
      >
        {serverError && (
          <Banner tone="danger" title="The server refused this">
            {serverError}
          </Banner>
        )}
      </ConfirmDialog>

      {/* Department recommendation: rationale mandatory, and the routing is
          spelled out so nobody believes they have just rejected someone. */}
      <ReasonDialog
        open={rationaleFor !== null}
        onClose={() => setRationaleFor(null)}
        onSubmit={(rationale) => rationaleFor && runRecommend(rationaleFor, rationale)}
        loading={recommend.isPending}
        serverError={serverError}
        tone={rationaleFor === 'recommend_reject' ? 'danger' : 'primary'}
        title={rationaleFor ? (DECISION_LABELS[rationaleFor] ?? 'Recommendation') : ''}
        description={`${application.candidate_name} — ${application.job_title}`}
        label="Rationale"
        confirmLabel="Submit recommendation"
        banner={
          rationaleFor === 'recommend_reject' ? (
            <Banner tone="info" title="This is a recommendation, not a rejection">
              Your assessment routes to the HR Head, who makes the final decision. The candidate is
              not rejected by this action.
            </Banner>
          ) : undefined
        }
      />

      {/* The Recruiter's screening rejection: reason mandatory (20 chars to
          250 words, enforced again by the server), closes the application and
          emails the candidate the professional rejection. */}
      <ReasonDialog
        open={screeningOut}
        onClose={() => setScreeningOut(false)}
        onSubmit={(reason) =>
          advance.mutate(
            { decision: 'screen_out', rationale: reason },
            {
              onSuccess: () => {
                setScreeningOut(false)
                afterSuccess('Application rejected. The candidate has been notified.')
              },
              onError: handleError,
            },
          )
        }
        loading={advance.isPending}
        serverError={serverError}
        tone="danger"
        title="Reject application"
        description={`${application.candidate_name} — ${application.job_title}`}
        label="Rejection reason"
        hint="Recorded permanently and shown in the application history. Up to 250 words. The candidate receives a professional rejection email that does not include this reason."
        confirmLabel="Reject application"
      />

      {/* HR Head's terminal rejection. */}
      <ReasonDialog
        open={rejecting}
        onClose={() => setRejecting(false)}
        onSubmit={(reason) =>
          reject.mutate(reason, {
            onSuccess: () => afterSuccess('Candidate rejected. The reason has been recorded.'),
            onError: handleError,
          })
        }
        loading={reject.isPending}
        serverError={serverError}
        title="Reject candidate"
        description={`${application.candidate_name} — ${application.job_title}`}
        label="Reason for rejection"
        confirmLabel="Reject candidate"
        banner={
          <Banner tone="danger" title="This is final">
            The candidate's application closes and the reason is stored permanently against their
            record. Only an administrator can reverse it, through a separately audited override.
          </Banner>
        }
      />

      {/* HR Head's selection. */}
      <ConfirmDialog
        open={confirming === 'select'}
        onClose={() => setConfirming(null)}
        onConfirm={() =>
          select.mutate(undefined, {
            onSuccess: () => afterSuccess('Candidate selected. You can now raise an offer.'),
            onError: handleError,
          })
        }
        loading={select.isPending}
        title="Select candidate"
        description={`${application.candidate_name} moves to the offer stage.`}
        confirmLabel="Select"
      />
    </div>
  )
}
