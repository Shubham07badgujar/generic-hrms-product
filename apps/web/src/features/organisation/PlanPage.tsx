/**
 * What this organization is on, and how much of it is used.
 *
 * READ ONLY, and that is the product decision rather than an unfinished
 * screen. A customer does not change their own plan through the HR product:
 * that is a commercial conversation, and an upgrade button here would put a
 * billing decision in the hands of whoever happens to hold the Admin role
 * this month. The endpoint offers no write either — this page could not
 * change the plan if it tried.
 *
 * So what it owes the reader is the two numbers they actually need: how many
 * seats are left before the next hire is refused, and which modules their plan
 * includes. Both come from the server; neither is computed here. `seats
 * remaining` in particular is the SAME function the bulk importer gates on, so
 * the number a person reads here and the number that refuses a 200-row staff
 * list cannot drift apart.
 *
 * FEATURE LABELS ARE THE ONE THING THIS FILE OWNS. The server sends codes;
 * the human names live here because they are presentation. A code with no
 * label still renders — as the code — rather than vanishing, since a feature
 * the customer is paying for must never be invisible because the SPA has not
 * been taught its name yet.
 */

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { Card, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import { ErrorState, LoadingBlock } from '@/components/ui/States'
import type { FeatureCode, MyPlan, SubscriptionStatus } from '@/lib/types'

const FEATURE_LABELS: Record<FeatureCode, string> = {
  core: 'Core HR',
  recruitment: 'Recruitment',
  onboarding: 'Onboarding',
  offboarding: 'Offboarding',
  attendance: 'Attendance',
  attendance_biometric: 'Biometric devices',
  leave: 'Leave',
  payroll: 'Payroll and statutory',
  assets: 'Asset tracking',
  it_accounts: 'Company email accounts',
  reporting: 'Reports and analytics',
}

const ALL_FEATURES = Object.keys(FEATURE_LABELS) as FeatureCode[]

/**
 * What each commercial state means to the person reading it.
 *
 * `past_due` deliberately does not say "your access will be cut off". It does
 * not stop anyone working — locking an HR department out of payroll on the
 * 30th because an invoice is late punishes the employees rather than the
 * buyer — so the copy says what is true: somebody needs to talk to accounts.
 */
const STATUS_COPY: Record<SubscriptionStatus, { label: string; tone: string; note: string }> = {
  trialing: {
    label: 'Trial',
    tone: 'bg-info-soft text-info-ink',
    note: 'You are on a trial. Everything works exactly as it will afterwards.',
  },
  active: {
    label: 'Active',
    tone: 'bg-success-soft text-success-ink',
    note: '',
  },
  past_due: {
    label: 'Past due',
    tone: 'bg-warning-soft text-warning-ink',
    note:
      'An invoice is outstanding. Nothing is switched off — your team keeps working ' +
      'while this is sorted out with your account manager.',
  },
  cancelled: {
    label: 'Cancelled',
    tone: 'bg-danger-soft text-danger-ink',
    note:
      'This subscription has been cancelled. Your records are retained for a period ' +
      'in which an administrator can still export them.',
  },
  expired: {
    label: 'Expired',
    tone: 'bg-danger-soft text-danger-ink',
    note: 'This subscription has ended. Your account manager can restore it.',
  },
}

function Badge({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${className}`}>
      {children}
    </span>
  )
}

export function PlanPage() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['org-plan'],
    queryFn: () => apiGet<MyPlan>('/org/plan/'),
  })

  if (isLoading) return <LoadingBlock label="Loading your plan" />
  if (error || !data) return <ErrorState error={error} onRetry={() => void refetch()} />

  const status = data.status ? STATUS_COPY[data.status] : null
  const included = new Set(data.features)
  const unlimited = data.employee_limit === null

  return (
    <>
      <PageHeader
        title="Plan and usage"
        description={
          'What your organization is on, and how much of it is in use. Changes to the ' +
          'plan itself go through your account manager.'
        }
      />

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-base font-medium text-ink">
              {data.plan?.name ?? 'No plan'}
            </p>
            <p className="mt-0.5 max-w-prose text-sm text-ink-muted">
              {data.plan?.description ??
                'This deployment is not on a subscription, so nothing here is limited.'}
            </p>
          </div>
          {status && <Badge className={status.tone}>{status.label}</Badge>}
        </div>
        {status?.note && (
          <p className="mt-3 max-w-prose text-sm text-ink-muted">{status.note}</p>
        )}
      </Card>

      <Section title="Usage" className="mt-4">
        <Card>
          <DescriptionList
            items={[
              { label: 'Employees', value: String(data.employees_used) },
              {
                label: 'Seat limit',
                value: unlimited ? 'Unlimited' : String(data.employee_limit),
              },
              {
                label: 'Seats remaining',
                value: unlimited
                  ? 'Unlimited'
                  : // The same number the bulk importer refuses a batch on.
                    `${data.seats_remaining ?? 0} before the next hire is refused`,
              },
              {
                label: 'Storage',
                value:
                  data.storage_limit_mb === null
                    ? 'Unlimited'
                    : // Measured and shown, never a wall: an upload that failed
                      // mid-payroll would be worse than no cap at all.
                      `${data.storage_limit_mb} MB included`,
              },
              ...(data.trial_ends_at
                ? [
                    {
                      label: 'Trial ends',
                      value: new Date(data.trial_ends_at).toLocaleDateString(),
                    },
                  ]
                : []),
            ]}
          />
        </Card>
      </Section>

      <Section
        title="Modules"
        description="What this plan includes. Anything not included is hidden from the menu and refused by the API."
        className="mt-4"
      >
        <Card>
          <ul className="grid gap-2 sm:grid-cols-2">
            {ALL_FEATURES.map((feature) => {
              const on = included.has(feature)
              return (
                <li key={feature} className="flex items-center gap-2 text-sm">
                  <span
                    aria-hidden
                    className={
                      'flex h-5 w-5 items-center justify-center rounded-full text-[11px] ' +
                      (on
                        ? 'bg-success-soft text-success-ink'
                        : 'bg-canvas text-ink-subtle')
                    }
                  >
                    {on ? '✓' : '—'}
                  </span>
                  <span className={on ? 'text-ink' : 'text-ink-subtle'}>
                    {FEATURE_LABELS[feature]}
                  </span>
                  <span className="sr-only">{on ? 'included' : 'not included'}</span>
                </li>
              )
            })}
            {/* A feature the server sends that this build has no name for. It
                is rendered as its code rather than dropped: a module the
                customer is paying for must not be invisible because the SPA is
                a version behind. */}
            {data.features
              .filter((feature) => !(feature in FEATURE_LABELS))
              .map((feature) => (
                <li key={feature} className="flex items-center gap-2 text-sm">
                  <span
                    aria-hidden
                    className="flex h-5 w-5 items-center justify-center rounded-full bg-success-soft text-[11px] text-success-ink"
                  >
                    ✓
                  </span>
                  <span className="text-ink">{feature}</span>
                </li>
              ))}
          </ul>
        </Card>
      </Section>
    </>
  )
}
