/**
 * The panels that make up an employee profile.
 *
 * A recurring pattern here: a section the API OMITTED is rendered as
 * "not permitted", never as an empty list. The backend leaves a section out
 * when the caller may not read it, and showing an empty table in that case
 * would tell the user something false — that there is nothing there.
 */

import { useState, type ReactNode } from 'react'
import { Card, CardHeader, DescriptionList } from '@/components/ui/Card'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner, type TimelineEntry } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/States'
import { Modal } from '@/components/ui/Modal'
import { ConfirmDialog, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Checkbox, Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { useAuth, usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import {
  downloadFile,
  useAccountActions,
  useAllocationActions,
  useAssets,
  useBulkAllocate,
  useDocumentActions,
  useDocumentTypes,
  useGenerateLetter,
  useOnboardingItemActions,
  useProbationActions,
  useRecordCompanyAccount,
  useUploadDocument,
} from '@/lib/lifecycleQueries'
import { formatDate, formatDateTime, humanize } from '@/lib/format'
import type {
  AssetAllocation,
  CompanyEmailAccount,
  EmployeeDocument,
  EmployeeLetter,
  EmployeeOnboarding,
  EmployeeProfile,
  OnboardingItem,
  ProbationReview,
  UUID,
} from '@/lib/types'

/** Shown wherever the API withheld a section. */
export function NotPermitted({ what }: { what: string }) {
  return (
    <Card>
      <EmptyState
        title={`${what} are not visible to you`}
        description={
          'Your role does not include this permission, so the server did not return this ' +
          'section. This is not the same as there being nothing here.'
        }
      />
    </Card>
  )
}

/* ============================================================== documents */

const DOCUMENT_TONES: Record<string, Tone> = {
  pending: 'warning',
  verified: 'success',
  rejected: 'danger',
  expired: 'neutral',
}

export function DocumentsSection({
  employeeId,
  documents,
  onChanged,
}: {
  employeeId: UUID
  documents: EmployeeDocument[] | undefined
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const types = useDocumentTypes()
  const upload = useUploadDocument(employeeId)
  const actions = useDocumentActions(employeeId)

  const [uploading, setUploading] = useState(false)
  const [rejecting, setRejecting] = useState<EmployeeDocument | null>(null)
  const [documentType, setDocumentType] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [expiresOn, setExpiresOn] = useState('')
  const [error, setError] = useState<string | undefined>()

  if (documents === undefined) return <NotPermitted what="Documents" />

  const mayUpload = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.CREATE)
  const mayVerify = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.APPROVE)
  const mayReject = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.REJECT)
  const selectedType = (types.data ?? []).find((item) => item.id === documentType)

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Documents"
          description="Collected and verified separately — an upload is not a verification."
          action={
            mayUpload && (
              <Button variant="primary" size="sm" onClick={() => setUploading(true)}>
                Upload document
              </Button>
            )
          }
        />

        {documents.length === 0 ? (
          <EmptyState compact title="No documents on file yet" />
        ) : (
          <ul className="divide-y divide-line">
            {documents.map((document) => (
              <li key={document.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-ink">{document.document_type_name}</p>
                    <Badge tone={DOCUMENT_TONES[document.status] ?? 'neutral'}>
                      {humanize(document.status)}
                    </Badge>
                    {document.expires_on && (
                      <span className="text-xs text-ink-subtle">
                        expires {formatDate(document.expires_on)}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-ink-muted">
                    {document.original_filename || 'file'} · uploaded{' '}
                    {formatDate(document.uploaded_at)}
                    {document.uploaded_by_email ? ` by ${document.uploaded_by_email}` : ''}
                  </p>
                  {document.status === 'verified' && document.verified_by_email && (
                    <p className="text-xs text-success-ink">
                      Verified by {document.verified_by_email} on{' '}
                      {formatDate(document.verified_at)}
                    </p>
                  )}
                  {document.rejection_reason && (
                    <p className="text-xs text-danger-ink">{document.rejection_reason}</p>
                  )}
                </div>

                <div className="flex shrink-0 gap-2">
                  {document.has_file && (
                    <Button
                      size="sm"
                      onClick={() =>
                        void downloadFile(
                          `/employee-documents/${document.id}/download/`,
                          document.original_filename || 'document',
                        )
                      }
                    >
                      Download
                    </Button>
                  )}
                  {/*
                    Two grants, checked separately. Vouching for a document and
                    refusing one are different authorities, so a role may hold
                    either without the other and the buttons follow suit. A
                    document that is no longer pending offers neither: the
                    decision is made, and a correction arrives as a new upload.
                  */}
                  {mayVerify && document.status === 'pending' && (
                    <Button
                      size="sm"
                      variant="primary"
                      loading={actions.verify.isPending}
                      onClick={() =>
                        actions.verify.mutate(document.id, {
                          onSuccess: () => {
                            toast.success('Document verified')
                            onChanged?.()
                          },
                          onError: (submitError) =>
                            toast.fromError(submitError, 'Could not verify'),
                        })
                      }
                    >
                      Verify
                    </Button>
                  )}
                  {mayReject && document.status === 'pending' && (
                    <Button size="sm" variant="danger-soft" onClick={() => setRejecting(document)}>
                      Reject
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={uploading}
        onClose={() => setUploading(false)}
        busy={upload.isPending}
        title="Upload a document"
        description="It will be recorded as awaiting verification."
        footer={
          <>
            <Button onClick={() => setUploading(false)} disabled={upload.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={upload.isPending}
              disabled={!documentType || !file}
              onClick={() => {
                setError(undefined)
                upload.mutate(
                  {
                    document_type: documentType,
                    file: file!,
                    ...(expiresOn ? { expires_on: expiresOn } : {}),
                  },
                  {
                    onSuccess: () => {
                      toast.success('Document uploaded', 'It is now awaiting verification.')
                      setUploading(false)
                      setDocumentType('')
                      setFile(null)
                      setExpiresOn('')
                      onChanged?.()
                    },
                    onError: (submitError) => {
                      if (submitError instanceof ApiError) setError(submitError.displayMessage)
                      toast.fromError(submitError, 'Upload failed')
                    },
                  },
                )
              }}
            >
              Upload
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          <Select
            label="Document type"
            required
            placeholder="Select a type"
            value={documentType}
            onChange={(event) => setDocumentType(event.target.value)}
            options={(types.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.name}${item.is_mandatory ? ' (required)' : ''}`,
            }))}
          />
          <div className="space-y-1.5">
            <label htmlFor="document-file" className="text-sm font-medium text-ink">
              File <span className="text-danger">*</span>
            </label>
            <input
              id="document-file"
              type="file"
              accept="application/pdf,image/*"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="w-full rounded-lg border border-line bg-surface p-2 text-sm"
            />
            <p className="text-xs text-ink-subtle">PDF or image, up to 10 MB.</p>
          </div>
          {selectedType?.requires_expiry && (
            <TextInput
              label="Expiry date"
              type="date"
              required
              value={expiresOn}
              onChange={(event) => setExpiresOn(event.target.value)}
              description={`A ${selectedType.name} must record when it expires.`}
            />
          )}
        </div>
      </Modal>

      <ReasonDialog
        open={rejecting !== null}
        onClose={() => setRejecting(null)}
        loading={actions.reject.isPending}
        title="Reject this document"
        description={rejecting?.document_type_name}
        label="Reason"
        confirmLabel="Reject document"
        onSubmit={(reason) =>
          rejecting &&
          actions.reject.mutate(
            { id: rejecting.id, reason },
            {
              onSuccess: () => {
                toast.success('Document rejected')
                setRejecting(null)
                onChanged?.()
              },
              onError: (submitError) => toast.fromError(submitError, 'Could not reject'),
            },
          )
        }
        banner={
          <Banner tone="info">
            The employee sees this reason, so say what needs to be different. The related
            checklist item reopens automatically.
          </Banner>
        }
      />
    </>
  )
}

/* ============================================================= onboarding */

const ITEM_TONES: Record<string, Tone> = {
  pending: 'neutral',
  in_progress: 'info',
  submitted: 'warning',
  completed: 'success',
  waived: 'brand',
  blocked: 'danger',
}

export function OnboardingSection({
  onboarding,
  employeeId,
  onChanged,
}: {
  onboarding: EmployeeOnboarding | null | undefined
  employeeId: UUID
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const { user } = useAuth()
  const toast = useToast()
  const actions = useOnboardingItemActions(employeeId)
  const [waiving, setWaiving] = useState<OnboardingItem | null>(null)

  if (onboarding === undefined) return <NotPermitted what="Onboarding details" />
  if (onboarding === null) {
    return (
      <Card>
        <EmptyState
          title="No onboarding checklist"
          description="This employee was created without one, or no template was configured at the time."
        />
      </Card>
    )
  }

  const mayEdit = permissions.can(RESOURCE.ONBOARDING, ACTION.EDIT)
  // The checklist's SUBJECT may finish their own lines and nothing else —
  // no ticking the manager's tasks, no waiving requirements away. HR roles
  // run onboarding, so they keep the full controls on any checklist,
  // their own included. The server enforces the same rule.
  const runsOnboarding =
    permissions.hasRole('admin') ||
    permissions.hasRole('hr_head') ||
    permissions.hasRole('hr_manager')
  const subjectOnly = user?.employee_id === employeeId && !runsOnboarding
  const percent = onboarding.total_count
    ? Math.round((onboarding.completed_count / onboarding.total_count) * 100)
    : 0

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Onboarding"
          description={onboarding.template_name || 'No template'}
          action={
            <Badge tone={onboarding.status === 'completed' ? 'success' : 'info'}>
              {humanize(onboarding.status)}
            </Badge>
          }
        />

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>
              {onboarding.completed_count} of {onboarding.total_count} done
            </span>
            {onboarding.outstanding_mandatory_count > 0 && (
              <span className="text-warning-ink">
                {onboarding.outstanding_mandatory_count} mandatory outstanding
              </span>
            )}
          </div>
          <div
            className="h-2 overflow-hidden rounded-full bg-canvas"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Onboarding progress"
          >
            <div
              className="h-full rounded-full bg-brand transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>

        <ul className="divide-y divide-line">
          {onboarding.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-ink">{item.title}</p>
                  <Badge tone={ITEM_TONES[item.status] ?? 'neutral'}>
                    {humanize(item.status)}
                  </Badge>
                  {item.is_mandatory && <Badge tone="warning">Required</Badge>}
                  {item.is_overdue && <Badge tone="danger">Overdue</Badge>}
                </div>
                <p className="text-xs text-ink-muted">
                  {humanize(item.kind)} · {humanize(item.owner)}
                  {item.assigned_to_name ? ` (${item.assigned_to_name})` : ''}
                  {item.due_date ? ` · due ${formatDate(item.due_date)}` : ''}
                </p>
                {item.notes && <p className="pt-1 text-xs text-ink-muted">{item.notes}</p>}
              </div>

              {mayEdit && !item.is_done && (
                <div className="flex shrink-0 gap-2">
                  {/* A document item is completed by uploading the document,
                      not by ticking a box — the API refuses the shortcut. */}
                  {item.kind !== 'document' && (!subjectOnly || item.owner === 'employee') && (
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() =>
                        actions.complete.mutate(
                          { id: item.id },
                          {
                            onSuccess: () => {
                              toast.success('Item completed')
                              onChanged?.()
                            },
                            onError: (error) => toast.fromError(error, 'Could not complete'),
                          },
                        )
                      }
                    >
                      Mark done
                    </Button>
                  )}
                  {!subjectOnly && (
                    <Button size="sm" onClick={() => setWaiving(item)}>
                      Waive
                    </Button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>

      <ReasonDialog
        open={waiving !== null}
        onClose={() => setWaiving(null)}
        loading={actions.waive.isPending}
        tone="primary"
        title="Waive this item"
        description={waiving?.title}
        label="Reason"
        confirmLabel="Waive item"
        onSubmit={(reason) =>
          waiving &&
          actions.waive.mutate(
            { id: waiving.id, reason },
            {
              onSuccess: () => {
                toast.success('Item waived')
                setWaiving(null)
                onChanged?.()
              },
              onError: (error) => toast.fromError(error, 'Could not waive'),
            },
          )
        }
        banner={
          <Banner tone="info">
            A waived item is recorded as waived, not as completed, so it stays visible in a
            later review.
          </Banner>
        }
      />
    </>
  )
}

/* ============================================================== probation */

export function ProbationSection({
  reviews,
  employee,
  onChanged,
}: {
  reviews: ProbationReview[] | undefined
  employee: EmployeeProfile['employee']
  onChanged?: () => void
}) {
  if (reviews === undefined) return <NotPermitted what="Probation reviews" />

  const open = reviews.find((review) => !review.is_decided)

  return (
    <div className="space-y-5">
      <Card className="space-y-4">
        <CardHeader title="Probation" />
        <DescriptionList
          columns={3}
          items={[
            { label: 'Status', value: <Badge tone="info">{humanize(employee.probation_status)}</Badge> },
            { label: 'Started', value: formatDate(employee.probation_start_date) },
            { label: 'Ends', value: formatDate(employee.probation_end_date) },
            { label: 'Confirmed on', value: formatDate(employee.confirmation_date) },
          ]}
        />
        {employee.probation_status === 'due' && (
          <Banner tone="warning" title="A decision is due">
            The probation period has ended. The system does not confirm anyone automatically —
            HR must record confirm, extend or terminate.
          </Banner>
        )}
      </Card>

      {open && <ProbationDecisionPanel review={open} onChanged={onChanged} />}

      {reviews.filter((review) => review.is_decided).length > 0 && (
        <Card className="space-y-4">
          <CardHeader title="Review history" />
          <div className="space-y-3">
            {reviews
              .filter((review) => review.is_decided)
              .map((review) => (
                <div key={review.id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge
                        tone={
                          review.decision === 'confirm'
                            ? 'success'
                            : review.decision === 'terminate'
                              ? 'danger'
                              : 'warning'
                        }
                      >
                        {humanize(review.decision)}
                      </Badge>
                      <span className="text-xs text-ink-muted">
                        for probation ending {formatDate(review.probation_end_date)}
                      </span>
                    </div>
                    <span className="text-xs text-ink-subtle">
                      {formatDateTime(review.decided_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-ink-muted">
                    Decided by {review.decided_by_email}
                    {review.reviewer_name ? ` · assessed by ${review.reviewer_name}` : ''}
                    {review.recommendation !== 'pending'
                      ? ` · recommended ${humanize(review.recommendation).toLowerCase()}`
                      : ''}
                  </p>
                  {review.rationale && (
                    <p className="mt-2 whitespace-pre-wrap text-sm text-ink-muted">
                      {review.rationale}
                    </p>
                  )}
                </div>
              ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function ProbationDecisionPanel({
  review,
  onChanged,
}: {
  review: ProbationReview
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = useProbationActions(review.id, review.employee)

  const [assessing, setAssessing] = useState(false)
  const [deciding, setDeciding] = useState<'confirm' | 'extend' | 'terminate' | null>(null)
  const [extendedTo, setExtendedTo] = useState('')
  const [recommendation, setRecommendation] = useState(review.recommendation)
  const [strengths, setStrengths] = useState(review.strengths)
  const [improvements, setImprovements] = useState(review.areas_for_improvement)
  const [performance, setPerformance] = useState(String(review.performance_rating ?? ''))
  const [error, setError] = useState<string | undefined>()

  const mayAssess = permissions.can(RESOURCE.PROBATION_REVIEW, ACTION.EDIT)
  const mayDecide = permissions.can(RESOURCE.PROBATION_REVIEW, ACTION.DECIDE)

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Open review"
          description={`For the probation ending ${formatDate(review.probation_end_date)}`}
          action={
            <div className="flex flex-wrap gap-2">
              {mayAssess && (
                <Button size="sm" onClick={() => setAssessing(true)}>
                  {review.reviewed_at ? 'Update assessment' : 'Record assessment'}
                </Button>
              )}
              {mayDecide && (
                <>
                  <Button size="sm" variant="primary" onClick={() => setDeciding('confirm')}>
                    Confirm
                  </Button>
                  <Button size="sm" onClick={() => setDeciding('extend')}>
                    Extend
                  </Button>
                  <Button size="sm" variant="danger-soft" onClick={() => setDeciding('terminate')}>
                    Terminate
                  </Button>
                </>
              )}
            </div>
          }
        />

        {!mayDecide && mayAssess && (
          <Banner tone="info" title="Your assessment is advisory">
            You record the review and a recommendation. The employment decision — confirm,
            extend or terminate — belongs to HR.
          </Banner>
        )}

        {review.reviewed_at ? (
          <DescriptionList
            columns={2}
            items={[
              { label: 'Reviewer', value: review.reviewer_name ?? '—' },
              { label: 'Recommendation', value: humanize(review.recommendation) },
              { label: 'Performance', value: review.performance_rating ?? '—' },
              { label: 'Reliability', value: review.reliability_rating ?? '—' },
              { label: 'Strengths', value: review.strengths || '—' },
              { label: 'To improve', value: review.areas_for_improvement || '—' },
            ]}
          />
        ) : (
          <Banner tone="neutral">No assessment has been recorded yet.</Banner>
        )}
      </Card>

      <Modal
        open={assessing}
        onClose={() => setAssessing(false)}
        busy={actions.assess.isPending}
        title="Probation assessment"
        description="Advisory. HR records the employment decision separately."
        footer={
          <>
            <Button onClick={() => setAssessing(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={actions.assess.isPending}
              disabled={recommendation === 'pending'}
              onClick={() =>
                actions.assess.mutate(
                  {
                    recommendation,
                    ...(performance ? { performance_rating: Number(performance) } : {}),
                    strengths,
                    areas_for_improvement: improvements,
                  },
                  {
                    onSuccess: () => {
                      toast.success('Assessment recorded')
                      setAssessing(false)
                      onChanged?.()
                    },
                    onError: (submitError) => toast.fromError(submitError, 'Could not save'),
                  },
                )
              }
            >
              Save assessment
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select
            label="Recommendation"
            required
            value={recommendation}
            onChange={(event) => setRecommendation(event.target.value as typeof recommendation)}
            options={[
              { value: 'confirm', label: 'Recommend confirmation' },
              { value: 'extend', label: 'Recommend extension' },
              { value: 'terminate', label: 'Recommend termination' },
            ]}
            placeholder="Select"
          />
          <Select
            label="Performance"
            value={performance}
            onChange={(event) => setPerformance(event.target.value)}
            placeholder="Optional"
            options={[1, 2, 3, 4, 5].map((score) => ({
              value: String(score),
              label: `${score} / 5`,
            }))}
          />
          <TextArea
            label="Strengths"
            rows={3}
            value={strengths}
            onChange={(event) => setStrengths(event.target.value)}
          />
          <TextArea
            label="Areas for improvement"
            rows={3}
            value={improvements}
            onChange={(event) => setImprovements(event.target.value)}
          />
        </div>
      </Modal>

      {/* Confirmation needs no defence; the other two do, and the API enforces it. */}
      <ConfirmDialog
        open={deciding === 'confirm'}
        onClose={() => setDeciding(null)}
        loading={actions.decide.isPending}
        title="Confirm this employee"
        description="Their employment is confirmed and the confirmation letter is generated."
        confirmLabel="Confirm employment"
        onConfirm={() =>
          actions.decide.mutate(
            { decision: 'confirm' },
            {
              onSuccess: () => {
                toast.success('Employee confirmed', 'The confirmation letter has been generated.')
                setDeciding(null)
                onChanged?.()
              },
              onError: (submitError) => toast.fromError(submitError, 'Could not confirm'),
            },
          )
        }
      >
        <Banner tone="info">
          The letter is produced because of this decision — it does not exist until now.
        </Banner>
      </ConfirmDialog>

      <ReasonDialog
        open={deciding === 'extend'}
        onClose={() => setDeciding(null)}
        loading={actions.decide.isPending}
        serverError={error}
        tone="primary"
        title="Extend probation"
        label="Rationale"
        confirmLabel="Extend probation"
        onSubmit={(rationale) => {
          setError(undefined)
          if (!extendedTo) {
            setError('A new end date is required.')
            return
          }
          actions.decide.mutate(
            { decision: 'extend', rationale, extended_to: extendedTo },
            {
              onSuccess: () => {
                toast.success('Probation extended')
                setDeciding(null)
                onChanged?.()
              },
              onError: (submitError) => {
                if (submitError instanceof ApiError) setError(submitError.displayMessage)
                toast.fromError(submitError, 'Could not extend')
              },
            },
          )
        }}
      >
        <TextInput
          label="New probation end date"
          type="date"
          required
          value={extendedTo}
          onChange={(event) => setExtendedTo(event.target.value)}
          description="Must be later than the current end date."
        />
      </ReasonDialog>

      <ReasonDialog
        open={deciding === 'terminate'}
        onClose={() => setDeciding(null)}
        loading={actions.decide.isPending}
        title="Terminate during probation"
        label="Rationale"
        confirmLabel="Terminate employment"
        onSubmit={(rationale) =>
          actions.decide.mutate(
            { decision: 'terminate', rationale },
            {
              onSuccess: () => {
                toast.success('Employment terminated')
                setDeciding(null)
                onChanged?.()
              },
              onError: (submitError) => toast.fromError(submitError, 'Could not terminate'),
            },
          )
        }
        banner={
          <Banner tone="danger" title="This ends someone's employment">
            The rationale is stored permanently against the review and is visible in the audit
            trail.
          </Banner>
        }
      />
    </>
  )
}

/* ================================================================ assets */

export function AssetsSection({
  allocations,
  employeeId,
  onboardingStatus,
  onChanged,
}: {
  allocations: AssetAllocation[] | undefined
  employeeId: UUID
  /** From the profile payload; undefined/null when no onboarding exists. */
  onboardingStatus?: 'in_progress' | 'completed' | 'cancelled' | null
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = useAllocationActions(employeeId)
  const [returning, setReturning] = useState<AssetAllocation | null>(null)
  const [writingOff, setWritingOff] = useState<AssetAllocation | null>(null)
  const [condition, setCondition] = useState('good')
  const [assigning, setAssigning] = useState(false)

  if (allocations === undefined) return <NotPermitted what="Asset allocations" />

  const current = allocations.filter((row) => row.status === 'active')
  const past = allocations.filter((row) => row.status !== 'active')
  const mayAssign = permissions.can(RESOURCE.ASSET_ALLOCATION, ACTION.CREATE)
  const mayReturn = permissions.can(RESOURCE.ASSET_ALLOCATION, ACTION.EDIT)
  const mayWriteOff = permissions.can(RESOURCE.ASSET_ALLOCATION, ACTION.DELETE)

  function row(allocation: AssetAllocation, showActions: boolean) {
    return (
      <li key={allocation.id} className="flex flex-wrap items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium text-ink">
              {allocation.asset_tag} · {allocation.asset_name}
            </p>
            <Badge
              tone={
                allocation.status === 'active'
                  ? 'info'
                  : allocation.status === 'written_off'
                    ? 'danger'
                    : 'neutral'
              }
            >
              {humanize(allocation.status)}
            </Badge>
          </div>
          <p className="text-xs text-ink-muted">
            {allocation.asset_category} · assigned {formatDate(allocation.allocated_at)}
            {allocation.allocated_by_email ? ` by ${allocation.allocated_by_email}` : ''}
          </p>
          {allocation.returned_at && (
            <p className="text-xs text-ink-muted">
              Returned {formatDate(allocation.returned_at)}
              {allocation.condition_at_return
                ? ` in ${humanize(allocation.condition_at_return).toLowerCase()} condition`
                : ''}
              {allocation.received_by_email ? ` to ${allocation.received_by_email}` : ''}
            </p>
          )}
          {allocation.write_off_reason && (
            <p className="text-xs text-danger-ink">{allocation.write_off_reason}</p>
          )}
        </div>

        {showActions && (
          <div className="flex shrink-0 gap-2">
            {mayReturn && (
              <Button size="sm" variant="primary" onClick={() => setReturning(allocation)}>
                Record return
              </Button>
            )}
            {mayWriteOff && (
              <Button size="sm" variant="danger-soft" onClick={() => setWritingOff(allocation)}>
                Write off
              </Button>
            )}
          </div>
        )}
      </li>
    )
  }

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Currently assigned"
          description="Company property held by this employee. Returnable items block an exit."
          action={
            mayAssign ? (
              <Button variant="primary" size="sm" onClick={() => setAssigning(true)}>
                Assign assets
              </Button>
            ) : undefined
          }
        />
        {mayAssign && onboardingStatus === 'in_progress' && (
          <Banner tone="info" title="Onboarding is still in progress">
            Assets are normally assigned once onboarding completes. Assigning one now also ticks
            the &ldquo;device and equipment&rdquo; item on the checklist.
          </Banner>
        )}
        {current.length === 0 ? (
          <EmptyState compact title="Nothing assigned" />
        ) : (
          <ul className="divide-y divide-line">{current.map((row_) => row(row_, true))}</ul>
        )}
      </Card>

      <Card className="space-y-4">
        <CardHeader title="History" description="Past assignments, kept permanently." />
        {past.length === 0 ? (
          <EmptyState compact title="No past assignments" />
        ) : (
          <ul className="divide-y divide-line">{past.map((row_) => row(row_, false))}</ul>
        )}
      </Card>

      {assigning && (
        <AssignAssetsModal
          employeeId={employeeId}
          onClose={() => setAssigning(false)}
          onAssigned={() => {
            setAssigning(false)
            onChanged?.()
          }}
        />
      )}

      <Modal
        open={returning !== null}
        onClose={() => setReturning(null)}
        busy={actions.returnAsset.isPending}
        size="sm"
        title="Record a return"
        description={returning ? `${returning.asset_tag} · ${returning.asset_name}` : ''}
        footer={
          <>
            <Button onClick={() => setReturning(null)}>Cancel</Button>
            <Button
              variant="primary"
              loading={actions.returnAsset.isPending}
              onClick={() =>
                returning &&
                actions.returnAsset.mutate(
                  { id: returning.id, condition },
                  {
                    onSuccess: () => {
                      toast.success('Return recorded')
                      setReturning(null)
                      onChanged?.()
                    },
                    onError: (error) => toast.fromError(error, 'Could not record the return'),
                  },
                )
              }
            >
              Record return
            </Button>
          </>
        }
      >
        <Select
          label="Condition on return"
          value={condition}
          onChange={(event) => setCondition(event.target.value)}
          options={[
            { value: 'new', label: 'As new' },
            { value: 'good', label: 'Good' },
            { value: 'fair', label: 'Fair' },
            { value: 'damaged', label: 'Damaged' },
            { value: 'unusable', label: 'Unusable' },
          ]}
          description="Damaged or unusable items go to maintenance rather than back into the pool."
        />
      </Modal>

      <ReasonDialog
        open={writingOff !== null}
        onClose={() => setWritingOff(null)}
        loading={actions.writeOff.isPending}
        title="Write off this asset"
        description={writingOff ? `${writingOff.asset_tag} · ${writingOff.asset_name}` : ''}
        label="Reason"
        confirmLabel="Write off"
        onSubmit={(reason) =>
          writingOff &&
          actions.writeOff.mutate(
            { id: writingOff.id, reason },
            {
              onSuccess: () => {
                toast.success('Asset written off')
                setWritingOff(null)
                onChanged?.()
              },
              onError: (error) => toast.fromError(error, 'Could not write off'),
            },
          )
        }
        banner={
          <Banner tone="danger" title="This accepts the loss of company property">
            The asset is marked lost and the allocation closes. This is the sanctioned way to
            let an exit complete when an item cannot be recovered.
          </Banner>
        }
      />
    </>
  )
}

/**
 * Pick several AVAILABLE assets and hand them over in one go.
 *
 * Only available assets are offered — an assigned asset simply is not in the
 * list — and the backend assigns the batch atomically, so a race with another
 * HR session refuses cleanly rather than half-assigning a kit.
 */
function AssignAssetsModal({
  employeeId,
  onClose,
  onAssigned,
}: {
  employeeId: UUID
  onClose: () => void
  onAssigned: () => void
}) {
  const toast = useToast()
  const available = useAssets({ status: 'available', page_size: '200' })
  const assign = useBulkAllocate(employeeId)
  const [selected, setSelected] = useState<Set<UUID>>(new Set())
  const [error, setError] = useState<string | undefined>()

  const rows = available.data?.data ?? []

  function toggle(id: UUID) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <Modal
      open
      onClose={onClose}
      busy={assign.isPending}
      size="md"
      title="Assign assets"
      description="Only available assets are listed. Everything ticked is assigned together."
      footer={
        <>
          <Button onClick={onClose} disabled={assign.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={assign.isPending}
            disabled={selected.size === 0}
            onClick={() => {
              setError(undefined)
              assign.mutate(
                { employee: employeeId, assets: [...selected] },
                {
                  onSuccess: (created) => {
                    toast.success(
                      created.length === 1
                        ? 'Asset assigned'
                        : `${created.length} assets assigned`,
                    )
                    onAssigned()
                  },
                  onError: (submitError) => {
                    if (submitError instanceof ApiError) setError(submitError.displayMessage)
                    toast.fromError(submitError, 'Could not assign')
                  },
                },
              )
            }}
          >
            {selected.size === 0
              ? 'Assign selected assets'
              : `Assign ${selected.size} selected ${selected.size === 1 ? 'asset' : 'assets'}`}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error && <Banner tone="danger">{error}</Banner>}
        {available.isLoading ? (
          <p className="text-sm text-ink-muted">Loading available assets…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            compact
            title="Nothing available"
            description="Every registered asset is currently assigned. Add assets on the Assets page or return one first."
          />
        ) : (
          <ul className="max-h-80 space-y-1 overflow-y-auto pr-1">
            {rows.map((asset) => (
              <li key={asset.id} className="rounded-lg border border-line px-3 py-2">
                <Checkbox
                  label={`${asset.name} — ${asset.asset_tag}`}
                  description={asset.serial_number ? `Serial ${asset.serial_number}` : undefined}
                  checked={selected.has(asset.id)}
                  onChange={() => toggle(asset.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  )
}

/* ======================================================= company account */

export function CompanyAccountSection({
  account,
  employeeId,
  onChanged,
}: {
  account: CompanyEmailAccount | null | undefined
  employeeId: UUID
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const record = useRecordCompanyAccount(employeeId)
  const actions = useAccountActions(account?.id ?? 'none', employeeId)

  const [recording, setRecording] = useState(false)
  const [email, setEmail] = useState('')
  const [provider, setProvider] = useState('google_workspace')
  const [externalId, setExternalId] = useState('')
  const [error, setError] = useState<string | undefined>()

  if (account === undefined) return <NotPermitted what="Company account details" />

  const mayRecord = permissions.can(RESOURCE.EMAIL_ACCOUNT, ACTION.CREATE)
  const mayEdit = permissions.can(RESOURCE.EMAIL_ACCOUNT, ACTION.EDIT)

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Company account"
          description="Recorded here; created in the provider's own console."
          action={
            !account &&
            mayRecord && (
              <Button variant="primary" size="sm" onClick={() => setRecording(true)}>
                Record account
              </Button>
            )
          }
        />

        <Banner tone="info" title="Credentials are handled outside this system">
          This platform stores no passwords for company accounts and has no field that could
          hold one. Create the mailbox in the provider's console and share the password with
          the employee through your organisation's secure channel.
        </Banner>

        {account ? (
          <>
            <DescriptionList
              columns={2}
              items={[
                { label: 'Email address', value: account.email_address },
                { label: 'Provider', value: humanize(account.provider) },
                {
                  label: 'Status',
                  value: (
                    <Badge tone={account.status === 'active' ? 'success' : 'warning'}>
                      {humanize(account.status)}
                    </Badge>
                  ),
                },
                { label: 'External ID', value: account.external_account_id || '—' },
                {
                  label: 'Requested',
                  value: `${formatDate(account.requested_at)}${
                    account.requested_by_email ? ` by ${account.requested_by_email}` : ''
                  }`,
                },
                {
                  label: 'Provisioned',
                  value: account.provisioned_at
                    ? `${formatDate(account.provisioned_at)}${
                        account.provisioned_by_email ? ` by ${account.provisioned_by_email}` : ''
                      }`
                    : '—',
                },
              ]}
            />
            {mayEdit && account.status !== 'active' && (
              <Button
                variant="primary"
                size="sm"
                loading={actions.provision.isPending}
                onClick={() =>
                  actions.provision.mutate(
                    {},
                    {
                      onSuccess: () => {
                        toast.success('Account marked active')
                        onChanged?.()
                      },
                      onError: (submitError) =>
                        toast.fromError(submitError, 'Could not update the account'),
                    },
                  )
                }
              >
                Mark as provisioned
              </Button>
            )}
          </>
        ) : (
          <EmptyState compact title="No company account recorded" />
        )}
      </Card>

      <Modal
        open={recording}
        onClose={() => setRecording(false)}
        busy={record.isPending}
        title="Record a company account"
        description="Track the mailbox you have already created in the provider's console."
        footer={
          <>
            <Button onClick={() => setRecording(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={record.isPending}
              disabled={!email}
              onClick={() => {
                setError(undefined)
                record.mutate(
                  { email_address: email, provider, external_account_id: externalId },
                  {
                    onSuccess: () => {
                      toast.success('Account recorded')
                      setRecording(false)
                      onChanged?.()
                    },
                    onError: (submitError) => {
                      if (submitError instanceof ApiError) setError(submitError.displayMessage)
                      toast.fromError(submitError, 'Could not record the account')
                    },
                  },
                )
              }}
            >
              Record account
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          <Banner tone="warning" title="Never enter a password here">
            There is no password field, and the API rejects any request carrying one.
          </Banner>
          <TextInput
            label="Company email address"
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Select
            label="Provider"
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            options={[
              { value: 'google_workspace', label: 'Google Workspace' },
              { value: 'microsoft_365', label: 'Microsoft 365' },
              { value: 'zoho', label: 'Zoho Mail' },
              { value: 'other', label: 'Other' },
            ]}
          />
          <TextInput
            label="Provider account ID"
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
            description="Optional. Reserved for a future directory integration."
          />
        </div>
      </Modal>
    </>
  )
}

/* =============================================================== letters */

const LETTER_LABELS: Record<string, string> = {
  offer: 'Offer letter',
  appointment: 'Appointment letter',
  joining: 'Joining letter',
  confirmation: 'Confirmation letter',
  extension: 'Probation extension letter',
  experience: 'Experience letter',
  relieving: 'Relieving letter',
  warning: 'Warning letter',
  other: 'Other',
}

export function LettersSection({
  letters,
  employee,
  onChanged,
}: {
  letters: EmployeeLetter[] | undefined
  employee: EmployeeProfile['employee']
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const generate = useGenerateLetter(employee.id)
  const [generating, setGenerating] = useState(false)
  const [letterType, setLetterType] = useState('appointment')
  const [error, setError] = useState<string | undefined>()

  if (letters === undefined) return <NotPermitted what="Letters" />

  const mayGenerate = permissions.can(RESOURCE.LETTER, ACTION.CREATE)
  const confirmed = employee.probation_status === 'confirmed'

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Letters"
          description="Generated from templates and stored as PDFs."
          action={
            mayGenerate && (
              <Button variant="primary" size="sm" onClick={() => setGenerating(true)}>
                Generate letter
              </Button>
            )
          }
        />

        {letters.length === 0 ? (
          <EmptyState compact title="No letters issued yet" />
        ) : (
          <ul className="divide-y divide-line">
            {letters.map((letter) => (
              <li key={letter.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-ink">
                      {LETTER_LABELS[letter.letter_type] ?? humanize(letter.letter_type)}
                    </p>
                    <Badge tone={letter.status === 'issued' ? 'success' : 'neutral'}>
                      {humanize(letter.status)}
                    </Badge>
                  </div>
                  <p className="text-xs text-ink-muted">
                    {letter.subject} · {formatDate(letter.generated_at)}
                    {letter.generated_by_email ? ` by ${letter.generated_by_email}` : ''}
                  </p>
                  <p className="text-xs text-ink-subtle">
                    Template: {letter.template_name || '—'} v{letter.template_version}
                  </p>
                </div>
                {letter.has_pdf && (
                  <Button
                    size="sm"
                    onClick={() =>
                      void downloadFile(
                        `/letters/${letter.id}/download/`,
                        `${letter.letter_type}-${employee.employee_code}.pdf`,
                      )
                    }
                  >
                    Download PDF
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={generating}
        onClose={() => setGenerating(false)}
        busy={generate.isPending}
        size="sm"
        title="Generate a letter"
        footer={
          <>
            <Button onClick={() => setGenerating(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={generate.isPending}
              onClick={() => {
                setError(undefined)
                generate.mutate(
                  { letter_type: letterType },
                  {
                    onSuccess: () => {
                      toast.success('Letter generated')
                      setGenerating(false)
                      onChanged?.()
                    },
                    onError: (submitError) => {
                      if (submitError instanceof ApiError) setError(submitError.displayMessage)
                      toast.fromError(submitError, 'Could not generate the letter')
                    },
                  },
                )
              }}
            >
              Generate
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          <Select
            label="Letter type"
            value={letterType}
            onChange={(event) => setLetterType(event.target.value)}
            options={[
              { value: 'appointment', label: 'Appointment letter' },
              { value: 'joining', label: 'Joining letter' },
              {
                value: 'confirmation',
                label: 'Confirmation letter',
                // The API refuses this before HR confirms; disabling it here
                // explains why rather than letting the user find out by failing.
                disabled: !confirmed,
              },
              { value: 'experience', label: 'Experience letter' },
            ]}
          />
          {!confirmed && (
            <Banner tone="info">
              A confirmation letter becomes available only after HR records a probation
              confirmation, because otherwise it would state something untrue.
            </Banner>
          )}
        </div>
      </Modal>
    </>
  )
}

/* =============================================================== history */

export function RecruitmentSourceCard({ profile }: { profile: EmployeeProfile }) {
  const source = profile.recruitment_source
  if (!source) return null

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Recruitment source"
        description="This employee was hired through the recruitment pipeline."
      />
      <DescriptionList
        columns={2}
        items={[
          { label: 'Candidate', value: source.candidate_name },
          { label: 'Source', value: humanize(source.source) },
          { label: 'Applied', value: formatDate(source.applied_on) },
        ]}
      />
      <a
        href={`/recruitment/candidates/${source.candidate_id}`}
        className="text-sm font-medium text-brand hover:underline"
      >
        Open the candidate record
      </a>
    </Card>
  )
}

/** A small helper so sections can share the same "changed" refresh. */
export function SectionStack({ children }: { children: ReactNode }) {
  return <div className="space-y-5">{children}</div>
}

export function buildProfileTimeline(profile: EmployeeProfile): TimelineEntry[] {
  const entries: TimelineEntry[] = []
  const employee = profile.employee

  entries.push({
    id: 'joined',
    tone: 'success',
    title: 'Joined the organisation',
    timestamp: formatDate(employee.date_of_joining),
    meta: `${employee.designation_title || 'No designation'} · ${employee.department_name ?? ''}`,
  })

  for (const review of profile.probation_reviews ?? []) {
    if (!review.is_decided) continue
    entries.push({
      id: `probation-${review.id}`,
      tone:
        review.decision === 'confirm'
          ? 'success'
          : review.decision === 'terminate'
            ? 'danger'
            : 'warning',
      title: `Probation ${humanize(review.decision).toLowerCase()}`,
      timestamp: formatDateTime(review.decided_at),
      meta: review.decided_by_email ?? undefined,
      body: review.rationale || undefined,
    })
  }

  for (const letter of profile.letters ?? []) {
    entries.push({
      id: `letter-${letter.id}`,
      tone: 'brand',
      title: `${LETTER_LABELS[letter.letter_type] ?? humanize(letter.letter_type)} issued`,
      timestamp: formatDateTime(letter.generated_at),
      meta: letter.generated_by_email ?? undefined,
    })
  }

  for (const allocation of profile.asset_allocations ?? []) {
    entries.push({
      id: `alloc-${allocation.id}`,
      tone: 'info',
      title: `${allocation.asset_tag} allocated`,
      timestamp: formatDateTime(allocation.allocated_at),
      meta: allocation.asset_name,
    })
    if (allocation.returned_at) {
      entries.push({
        id: `return-${allocation.id}`,
        tone: 'neutral',
        title: `${allocation.asset_tag} returned`,
        timestamp: formatDateTime(allocation.returned_at),
      })
    }
  }

  return entries.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
}
