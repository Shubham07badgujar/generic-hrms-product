/**
 * The offer.
 *
 * Gated on OFFER/CREATE, which the matrix grants to HR Head only — a recruiter
 * runs the whole pipeline and still cannot commit the company to terms. The
 * panel only appears once the candidate is SELECTED, because the service
 * refuses an offer at any earlier status.
 */

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, DescriptionList } from '@/components/ui/Card'
import { Modal } from '@/components/ui/Modal'
import { Select, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { OfferStatusBadge } from '@/components/ui/Badge'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/components/ui/Toast'
import {
  useCreateOffer,
  useDesignations,
  useCurrentEmployees,
  useOfferAction,
  useOffers,
} from '@/lib/queries'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError, downloadFile } from '@/lib/api'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/format'
import type { Application } from '@/lib/types'

export function OfferPanel({
  application,
  onChanged,
}: {
  application: Application
  onChanged?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()

  // Fire the query only for callers who may see offers at all — an
  // interviewer opening the application otherwise collects a 403 for a
  // panel that renders nothing for them anyway.
  const offers = useOffers(
    { application: application.id },
    { enabled: permissions.can(RESOURCE.OFFER) },
  )
  const offer = offers.data?.data?.[0]

  const createOffer = useCreateOffer()
  const actions = useOfferAction(offer?.id ?? 'none')

  const designations = useDesignations({ enabled: permissions.can(RESOURCE.DESIGNATION) })
  const managers = useCurrentEmployees()

  const [creating, setCreating] = useState(false)
  const [sending, setSending] = useState(false)
  const [responding, setResponding] = useState<'accept' | 'decline' | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const [ctc, setCtc] = useState('')
  const [joiningDate, setJoiningDate] = useState('')
  const [validUntil, setValidUntil] = useState('')
  const [designation, setDesignation] = useState('')
  const [reportingManager, setReportingManager] = useState('')

  const mayCreate = permissions.can(RESOURCE.OFFER, ACTION.CREATE)
  const mayEdit = permissions.can(RESOURCE.OFFER, ACTION.EDIT)
  const mayView = permissions.can(RESOURCE.OFFER)

  if (!mayView) return null

  // Before selection there is nothing to show and nothing to do.
  if (!offer && application.status !== 'selected') return null

  if (!offer) {
    return (
      <Card className="space-y-4">
        <CardHeader
          title="Offer"
          description="This candidate has been selected. The next step is to raise an offer."
          action={
            mayCreate && (
              <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                Create offer
              </Button>
            )
          }
        />
        {!mayCreate && (
          <Banner tone="info">
            Offers are raised by the HR Head. You can follow progress here once one exists.
          </Banner>
        )}

        <Modal
          open={creating}
          onClose={() => setCreating(false)}
          busy={createOffer.isPending}
          title="Create offer"
          description={`${application.candidate_name} — ${application.job_title}`}
          footer={
            <>
              <Button onClick={() => setCreating(false)} disabled={createOffer.isPending}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={createOffer.isPending}
                disabled={!ctc || !joiningDate}
                onClick={() => {
                  setFieldErrors({})
                  createOffer.mutate(
                    {
                      application: application.id,
                      offered_ctc: ctc,
                      joining_date: joiningDate,
                      ...(validUntil ? { valid_until: validUntil } : {}),
                      ...(designation ? { designation } : {}),
                      ...(reportingManager ? { reporting_manager: reportingManager } : {}),
                    },
                    {
                      onSuccess: () => {
                        toast.success('Offer created as a draft')
                        setCreating(false)
                        void offers.refetch()
                        onChanged?.()
                      },
                      onError: (error) => {
                        if (error instanceof ApiError) setFieldErrors(error.fieldErrors)
                        toast.fromError(error, 'Offer not created')
                      },
                    },
                  )
                }}
              >
                Create draft
              </Button>
            </>
          }
        >
          <div className="space-y-4">
            <TextInput
              label="Annual CTC"
              required
              inputMode="decimal"
              placeholder="600000.00"
              value={ctc}
              onChange={(event) => setCtc(event.target.value)}
              error={fieldErrors.offered_ctc}
              description="Stored as an exact decimal — no rounding."
            />
            <TextInput
              label="Joining date"
              type="date"
              required
              value={joiningDate}
              onChange={(event) => setJoiningDate(event.target.value)}
              error={fieldErrors.joining_date}
            />
            <TextInput
              label="Valid until"
              type="date"
              value={validUntil}
              onChange={(event) => setValidUntil(event.target.value)}
              error={fieldErrors.valid_until}
            />
            <Select
              label="Designation"
              placeholder="Use the job's designation"
              value={designation}
              onChange={(event) => setDesignation(event.target.value)}
              options={(designations.data ?? []).map((item) => ({
                value: item.id,
                label: item.title,
              }))}
              error={fieldErrors.designation}
            />
            <Select
              label="Reporting manager"
              placeholder="Decide at conversion"
              value={reportingManager}
              onChange={(event) => setReportingManager(event.target.value)}
              options={managers.rows.map((item) => ({
                value: item.id,
                label: `${item.full_name} — ${item.designation_title || item.department_name || ''}`,
              }))}
              error={fieldErrors.reporting_manager}
              description="Validated against the hierarchy rules when the candidate is converted."
            />
          </div>
        </Modal>
      </Card>
    )
  }

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Offer"
        action={
          <div className="flex items-center gap-2">
            <OfferStatusBadge status={offer.status} />
            <Button
              size="sm"
              variant="secondary"
              title={
                offer.status === 'draft'
                  ? 'Preview the letter exactly as Send offer will produce it'
                  : 'The letter the candidate received'
              }
              onClick={() =>
                void downloadFile(
                  `/offers/${offer.id}/letter/`,
                  'offer-letter.pdf',
                ).catch(() => undefined)
              }
            >
              Offer letter (PDF)
            </Button>
            {mayEdit && offer.status === 'draft' && (
              <Button size="sm" variant="primary" onClick={() => setSending(true)}>
                Send offer
              </Button>
            )}
            {mayEdit && offer.status === 'sent' && (
              <>
                <Button size="sm" variant="primary" onClick={() => setResponding('accept')}>
                  Record acceptance
                </Button>
                <Button size="sm" variant="danger-soft" onClick={() => setResponding('decline')}>
                  Record decline
                </Button>
              </>
            )}
          </div>
        }
      />

      <DescriptionList
        columns={2}
        items={[
          { label: 'Annual CTC', value: formatCurrency(offer.offered_ctc) },
          { label: 'Joining date', value: formatDate(offer.joining_date) },
          { label: 'Valid until', value: formatDate(offer.valid_until) },
          { label: 'Sent', value: formatDateTime(offer.sent_at) },
          { label: 'Responded', value: formatDateTime(offer.responded_at) },
        ]}
      />

      <ConfirmDialog
        open={sending}
        onClose={() => setSending(false)}
        loading={actions.send.isPending}
        title="Send this offer"
        description={`${application.candidate_name} will be recorded as having received the offer.`}
        confirmLabel="Send offer"
        onConfirm={() =>
          actions.send.mutate(undefined, {
            onSuccess: () => {
              toast.success('Offer sent')
              setSending(false)
              void offers.refetch()
              onChanged?.()
            },
            onError: (error) => toast.fromError(error, 'Could not send the offer'),
          })
        }
      />

      <ConfirmDialog
        open={responding !== null}
        onClose={() => setResponding(null)}
        loading={actions.respond.isPending}
        tone={responding === 'decline' ? 'danger' : 'primary'}
        title={responding === 'decline' ? 'Record a declined offer' : 'Record an accepted offer'}
        description={
          responding === 'decline'
            ? 'The candidacy closes and the data-retention clock starts for this candidate.'
            : 'The candidate can then be converted into an employee.'
        }
        confirmLabel={responding === 'decline' ? 'Record decline' : 'Record acceptance'}
        onConfirm={() =>
          actions.respond.mutate(
            { accepted: responding === 'accept' },
            {
              onSuccess: () => {
                toast.success('Response recorded')
                setResponding(null)
                void offers.refetch()
                onChanged?.()
              },
              onError: (error) => toast.fromError(error, 'Could not record the response'),
            },
          )
        }
      />
    </Card>
  )
}
