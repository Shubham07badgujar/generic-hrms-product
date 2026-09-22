/**
 * One customer: what state they are in, what they are on, and the three things
 * an operator may change about them.
 *
 * THREE, and the write surface is thin on purpose. Change the plan, move the
 * commercial status, override the seat limit — each a thin wrapper over a
 * service that already holds the rules, the row lock and the audit row. There
 * is deliberately no control here that edits a customer's own configuration,
 * and none that deletes them: retiring a customer is a lifecycle sequence with
 * waiting periods and an export window, not a button.
 *
 * REFUSALS ARE SHOWN, NOT PRE-EMPTED. A downgrade below the current headcount
 * comes back 422 with the service's own sentence ("this plan allows 50 active
 * employees; you have 118"), and the console prints it. Re-implementing that
 * rule here to grey out the option would give the operator a second opinion
 * that can drift from the first — and the `force` checkbox exists precisely
 * because the service, not this screen, decides when an override is allowed
 * and demands a reason for it.
 *
 * WHY A REASON IS REQUIRED in two of the three forms: "why does this customer
 * have 400 seats on a 50-seat plan", asked a year later, has to be answerable
 * from the audit trail. The database constraint agrees, so a blank reason is
 * refused whatever this form does.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Card, CardHeader, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import { Select, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { QueryBoundary } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/lib/api'
import { formatDate, humanize } from '@/lib/format'
import {
  usePlatformOrganization,
  usePlatformPlans,
  useArchiveOrganization,
  useChangePlan,
  useEndSupport,
  useRequestSupport,
  useResendInvitation,
  useSeatOverride,
  useSetSubscriptionStatus,
  useSupportConfiguration,
  useSupportGrants,
} from './queries'
import { OrganizationStatusBadge, SubscriptionStatusBadge } from './status'
import type { InvitationResend, PlatformOrganization, SupportConfiguration } from './types'

/** The commercial transitions. The service decides which are legal from here. */
const SUBSCRIPTION_STATUSES = [
  { value: 'trialing', label: 'Trialing' },
  { value: 'active', label: 'Active' },
  { value: 'past_due', label: 'Past due' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'expired', label: 'Expired' },
]

function useRefusal() {
  const [refusal, setRefusal] = useState<string | null>(null)
  const toast = useToast()
  const run = async (action: () => Promise<unknown>, success: string) => {
    setRefusal(null)
    try {
      await action()
      toast.success(success)
      return true
    } catch (error) {
      // The service's own sentence, verbatim. It knows the rule; this screen
      // only knows that the rule said no.
      setRefusal(error instanceof ApiError ? error.displayMessage : 'The change was refused.')
      return false
    }
  }
  return { refusal, run }
}

function ChangePlanCard({ organization }: { organization: PlatformOrganization }) {
  const plans = usePlatformPlans()
  const changePlan = useChangePlan(organization.id)
  const { refusal, run } = useRefusal()
  const [plan, setPlan] = useState(organization.subscription?.plan_code ?? '')
  const [reason, setReason] = useState('')
  const [force, setForce] = useState(false)

  return (
    <Card>
      <CardHeader
        title="Plan"
        description="Upgrades take effect immediately. A downgrade never deletes data — the modules it removes become read-only for a grace window."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}
        <Select
          label="Plan"
          value={plan}
          onChange={(event) => setPlan(event.target.value)}
          options={(plans.data?.data ?? []).map((row) => ({
            value: row.code,
            label:
              row.employee_limit === null
                ? `${row.name} — unlimited seats`
                : `${row.name} — ${row.employee_limit} seats`,
          }))}
          placeholder="Select a plan"
        />
        <TextInput
          label="Reason"
          hint="Recorded in the audit trail. Required when forcing a downgrade below the headcount."
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <label className="flex items-start gap-2 text-sm text-ink-muted">
          <input
            type="checkbox"
            checked={force}
            onChange={(event) => setForce(event.target.checked)}
            className="mt-0.5"
          />
          <span>
            Allow a seat limit below this customer's current headcount of{' '}
            {organization.employee_count}.
          </span>
        </label>
        <Button
          variant="primary"
          disabled={!plan || plan === organization.subscription?.plan_code}
          loading={changePlan.isPending}
          onClick={() =>
            void run(
              () => changePlan.mutateAsync({ plan, reason, force }),
              'Plan changed.',
            )
          }
        >
          Change plan
        </Button>
      </div>
    </Card>
  )
}

function SubscriptionStatusCard({ organization }: { organization: PlatformOrganization }) {
  const setStatus = useSetSubscriptionStatus(organization.id)
  const { refusal, run } = useRefusal()
  const [status, setNext] = useState(organization.subscription?.status ?? '')
  const [reason, setReason] = useState('')

  return (
    <Card>
      <CardHeader
        title="Subscription status"
        description="Commercial state. The organization's own status follows it through one service, so the two cannot disagree."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}
        {/*
          Said plainly because it is the rule operators get wrong: an unpaid
          invoice does not lock an HR department out of payroll on the 30th.
          It warns; it does not stop the work the employees depend on.
        */}
        <p className="text-sm text-ink-muted">
          Past due warns and switches nothing off. Cancelling stops writes and leaves a window
          in which the customer can still export their records.
        </p>
        <Select
          label="Status"
          value={status}
          onChange={(event) => setNext(event.target.value)}
          options={SUBSCRIPTION_STATUSES}
          placeholder="Select a status"
        />
        <TextInput
          label="Reason"
          hint="Recorded against the organization in the audit trail."
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <Button
          variant="primary"
          disabled={!status || status === organization.subscription?.status}
          loading={setStatus.isPending}
          onClick={() =>
            void run(
              () => setStatus.mutateAsync({ status, reason }),
              'Subscription status updated.',
            )
          }
        >
          Update status
        </Button>
      </div>
    </Card>
  )
}

/**
 * The administrator's invitation, resendable only while nobody has used it.
 *
 * The button exists only while `admin_invitation_pending` says an
 * administrator has never signed in, and the service refuses on the same test
 * -- so an operator is never offered a reset of a customer administrator who
 * is already working. That line is the point of the feature: resending an
 * unused invitation is support; resetting a live customer credential would be
 * the platform reaching into the customer's accounts.
 *
 * Confirmed first, because a resend INVALIDATES the previous temporary
 * password. Two quick clicks send two mails and only the second works, and the
 * operator should know that before they click rather than after the customer
 * calls.
 *
 * The result names who was mailed and whether each send went. No password: it
 * went to the administrator's own address and nowhere else.
 */
function InvitationCard({ organization }: { organization: PlatformOrganization }) {
  const resend = useResendInvitation(organization.id)
  const toast = useToast()
  const [refusal, setRefusal] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<InvitationResend | null>(null)

  // Not `useRefusal().run`, whose fixed success toast would announce "resent"
  // for a mail that failed -- the one claim this feature exists not to make.
  // The toast follows what the server says actually went.
  async function confirm() {
    setConfirming(false)
    setRefusal(null)
    try {
      const outcome = await resend.mutateAsync()
      setResult(outcome)
      if (outcome.invitations.every((item) => item.sent)) {
        toast.success('Invitation resent.')
      } else {
        toast.warning('The invitation could not be sent.')
      }
    } catch (error) {
      setRefusal(error instanceof ApiError ? error.displayMessage : 'The resend was refused.')
    }
  }

  return (
    <Card>
      <CardHeader
        title="Administrator invitation"
        description="For an administrator who has never signed in. Once they have, account recovery is the customer's, not the platform's."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}
        {result && (
          <ul className="space-y-1 text-sm">
            {result.invitations.map((item) => (
              <li key={item.email} className={item.sent ? 'text-ink' : 'text-danger-ink'}>
                {item.sent
                  ? `Sent to ${item.email}.`
                  : `Could not be sent to ${item.email}. Nothing was delivered; try again or check the mail settings.`}
              </li>
            ))}
          </ul>
        )}
        {organization.admin_invitation_pending ? (
          <Button
            variant="primary"
            loading={resend.isPending}
            onClick={() => setConfirming(true)}
          >
            Resend invitation
          </Button>
        ) : (
          <p className="text-sm text-ink-muted">
            Every administrator has signed in. There is no invitation to resend.
          </p>
        )}
      </div>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void confirm()}
        title="Resend the invitation?"
        description="A new temporary password is emailed to the administrator. The one in any earlier invitation stops working."
        confirmLabel="Resend"
        loading={resend.isPending}
      />
    </Card>
  )
}

/**
 * The end of a customer, as far as the console goes: ARCHIVE.
 *
 * Offered only for a cancelled organization, and the service refuses until
 * the customer's 90-day export window has closed -- archiving earlier would
 * take away an export they were promised. Archiving deletes nothing; it starts
 * the year after which the data MAY be purged.
 *
 * Purge is deliberately not on this page. It is the one irreversible thing
 * the product does, and it is a management command on the server with the
 * organization's slug typed twice. The card says so, so an operator looking
 * for the button learns why there is not one.
 */
function LifecycleCard({ organization }: { organization: PlatformOrganization }) {
  const archive = useArchiveOrganization(organization.id)
  const { refusal, run } = useRefusal()
  const [reason, setReason] = useState('')

  if (organization.purged_at) {
    return (
      <Card>
        <CardHeader title="Lifecycle" />
        <p className="mt-4 text-sm text-ink-muted">
          Purged on {formatDate(organization.purged_at)}. This record and a scrubbed audit
          trail are all that remain.
        </p>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader
        title="Lifecycle"
        description="Cancelled, then archived after the 90-day export window, then purgeable a year later."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}
        {organization.status === 'cancelled' && (
          <>
            <TextInput
              label="Reason for archiving"
              required
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Button
              variant="danger-soft"
              disabled={!reason.trim()}
              loading={archive.isPending}
              onClick={() =>
                void run(() => archive.mutateAsync({ reason }), 'Organization archived.')
              }
            >
              Archive organization
            </Button>
          </>
        )}
        {organization.status === 'archived' && organization.archived_at && (
          <p className="text-sm text-ink-muted">
            Archived on {formatDate(organization.archived_at)}. Its data may be purged a year
            after that date.
          </p>
        )}
        <p className="text-xs text-ink-subtle">
          Purging is not available here. It is irreversible, so it is a server command:{' '}
          <code>manage.py purge_organization {organization.slug} --confirm {organization.slug}</code>
        </p>
      </div>
    </Card>
  )
}

/**
 * Asking this customer to let support see its configuration.
 *
 * ASKS; DOES NOT GRANT. Nothing on this card can make the customer's setup
 * visible -- their administrator approves in their own application, for 24
 * hours, and can end it sooner. Until then "View configuration" is not
 * offered, and the API would refuse it anyway.
 *
 * What an approval shows is configuration from a fixed server-side list,
 * never a person. It is fetched on demand and not cached: each click is one
 * look, recorded in the customer's audit trail, and a copy lingering in the
 * browser after the grant ended would be access the customer did not give.
 */
function SupportCard({ organization }: { organization: PlatformOrganization }) {
  const grants = useSupportGrants()
  const request = useRequestSupport(organization.id)
  const configuration = useSupportConfiguration()
  const end = useEndSupport()
  const { refusal, run } = useRefusal()
  const [reason, setReason] = useState('')
  const [shown, setShown] = useState<SupportConfiguration | null>(null)

  const mine = (grants.data?.data ?? []).filter(
    (grant) => grant.organization_slug === organization.slug,
  )
  const live = mine.find((grant) => grant.usable)
  const pending = mine.find((grant) => grant.status === 'requested')

  async function look(grantId: string) {
    await run(async () => setShown(await configuration.mutateAsync(grantId)), 'Configuration loaded.')
  }

  return (
    <Card>
      <CardHeader
        title="Support access"
        description="Read-only access to this customer's configuration, with their administrator's approval, for 24 hours."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}

        {live ? (
          <div className="space-y-3">
            <p className="text-sm text-ink">
              Approved until {formatDate(live.expires_at)}.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                loading={configuration.isPending}
                onClick={() => void look(live.id)}
              >
                View configuration
              </Button>
              <Button
                variant="secondary"
                loading={end.isPending}
                onClick={() => {
                  setShown(null)
                  void run(() => end.mutateAsync(live.id), 'Access ended.')
                }}
              >
                End access
              </Button>
            </div>
          </div>
        ) : pending ? (
          <p className="text-sm text-ink-muted">
            Requested. Waiting for the customer's administrator to decide.
          </p>
        ) : (
          <>
            <TextInput
              label="Why you need to see their configuration"
              hint="At least 20 characters. The customer's administrator decides on the strength of this sentence."
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Button
              variant="primary"
              disabled={reason.trim().length < 20}
              loading={request.isPending}
              onClick={() =>
                void run(async () => {
                  await request.mutateAsync({ reason })
                  setReason('')
                }, 'Request sent to the customer.')
              }
            >
              Request access
            </Button>
          </>
        )}

        {shown && live && (
          <div className="space-y-2">
            {Object.entries(shown.tables).map(([label, rows]) => (
              <details key={label} className="rounded-lg border border-line px-3 py-2">
                <summary className="cursor-pointer text-sm text-ink">
                  {label} <span className="text-ink-subtle">({rows.length})</span>
                </summary>
                <pre className="mt-2 max-h-64 overflow-auto text-2xs text-ink-muted">
                  {JSON.stringify(rows, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

function SeatOverrideCard({ organization }: { organization: PlatformOrganization }) {
  const override = useSeatOverride(organization.id)
  const { refusal, run } = useRefusal()
  const current = organization.subscription?.employee_limit_override
  const [limit, setLimit] = useState(current === null || current === undefined ? '' : String(current))
  const [reason, setReason] = useState(organization.subscription?.override_reason ?? '')

  const parsed = limit.trim() === '' ? null : Number(limit)
  const invalid = parsed !== null && (!Number.isInteger(parsed) || parsed < 0)

  return (
    <Card>
      <CardHeader
        title="Seat override"
        description="A seat count this customer's plan does not carry. Clearing it defers to the plan again."
      />
      <div className="mt-4 space-y-4">
        {refusal && (
          <Banner tone="danger" title="Refused">
            {refusal}
          </Banner>
        )}
        <TextInput
          label="Employee limit"
          inputMode="numeric"
          hint="Leave blank to remove the override and use the plan's own limit."
          value={limit}
          error={invalid ? 'Enter a whole number of seats, or leave blank.' : undefined}
          onChange={(event) => setLimit(event.target.value)}
        />
        <TextInput
          label="Reason"
          required
          hint="Required by the API and by a database constraint. Clearing the override clears its reason with it."
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <Button
          variant="primary"
          disabled={invalid || !reason.trim()}
          loading={override.isPending}
          onClick={() =>
            void run(
              () => override.mutateAsync({ employee_limit: parsed, reason }),
              'Seat override saved.',
            )
          }
        >
          Save override
        </Button>
      </div>
    </Card>
  )
}

export function PlatformOrganizationDetailPage() {
  const { id } = useParams<{ id: string }>()
  const query = usePlatformOrganization(id)

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      data={query.data}
      onRetry={() => void query.refetch()}
    >
      {(organization) => {
        const subscription = organization.subscription
        return (
          <>
            <PageHeader
              breadcrumbs={
                <Link to="/platform/organizations" className="text-xs text-ink-muted hover:underline">
                  ← Organizations
                </Link>
              }
              title={organization.name}
              description={organization.legal_name || organization.slug}
              meta={
                <>
                  <OrganizationStatusBadge status={organization.status} />
                  {subscription && <SubscriptionStatusBadge status={subscription.status} />}
                </>
              }
            />

            <Section title="Account">
              <Card>
                <DescriptionList
                  items={[
                    { label: 'Slug', value: organization.slug },
                    { label: 'Primary email', value: organization.primary_email || '—' },
                    {
                      label: 'Location',
                      value:
                        [organization.city, organization.state, organization.country]
                          .filter(Boolean)
                          .join(', ') || '—',
                    },
                    { label: 'Timezone', value: organization.timezone },
                    { label: 'Currency', value: organization.currency },
                    { label: 'Created', value: formatDate(organization.created_at) },
                    {
                      label: 'Active employees',
                      value: organization.employee_count,
                    },
                    { label: 'User accounts', value: organization.member_count },
                  ]}
                  columns={3}
                />
              </Card>
            </Section>

            <Section title="Subscription">
              <Card>
                {subscription ? (
                  <DescriptionList
                    items={[
                      { label: 'Plan', value: `${subscription.plan_name} (${subscription.plan_code})` },
                      { label: 'Status', value: humanize(subscription.status) },
                      {
                        label: 'Employee limit',
                        value:
                          subscription.employee_limit === null
                            ? 'Unlimited'
                            : subscription.employee_limit,
                      },
                      {
                        label: 'Override',
                        value:
                          subscription.employee_limit_override === null
                            ? '—'
                            : `${subscription.employee_limit_override} — ${subscription.override_reason}`,
                      },
                      { label: 'Started', value: formatDate(subscription.started_at) },
                      { label: 'Ends', value: formatDate(subscription.ends_at) },
                      {
                        label: 'Features',
                        value:
                          subscription.enabled_features
                            .map((feature) => humanize(feature))
                            .join(', ') || '—',
                      },
                      {
                        label: 'Read-only grace',
                        value: `${subscription.read_only_grace_days} days`,
                      },
                    ]}
                    columns={3}
                  />
                ) : (
                  /* A real state, not an error: a self-hosted single-company
                     install sells nothing and has no subscription row. */
                  <p className="text-sm text-ink-muted">
                    This organization has no active subscription. On a deployment that sells
                    nothing that is expected; otherwise the plan below creates one.
                  </p>
                )}
              </Card>
            </Section>

            <Section title="Changes">
              <div className="grid gap-4 lg:grid-cols-2">
                <ChangePlanCard organization={organization} />
                <SubscriptionStatusCard organization={organization} />
                {subscription && <SeatOverrideCard organization={organization} />}
                <InvitationCard organization={organization} />
                <LifecycleCard organization={organization} />
                <SupportCard organization={organization} />
              </div>
            </Section>
          </>
        )
      }}
    </QueryBoundary>
  )
}
