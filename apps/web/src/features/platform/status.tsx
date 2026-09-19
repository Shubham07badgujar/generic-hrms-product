/**
 * How the console reads an organization's two statuses.
 *
 * TWO, and keeping them visibly separate is the point. `Organization.status`
 * is what decides access — it is the field `resolve_context()` reads, and a
 * suspended organization's users are refused whatever their subscription says.
 * `Subscription.status` is commercial state. They move together because a
 * subscription transition writes the organization's status through one
 * service, but they are not the same fact, and a console that showed a single
 * merged "status" would make the difference impossible to see at exactly the
 * moment an operator needs it.
 *
 * An unknown code renders as itself rather than disappearing: a status this
 * file has not been taught is still a status the operator must be able to see.
 */

import { Badge, type Tone } from '@/components/ui/Badge'
import { humanize } from '@/lib/format'
import type { PlatformOrganizationStatus } from './types'
import type { SubscriptionStatus } from '@/lib/types'

const ORG_TONES: Record<PlatformOrganizationStatus, Tone> = {
  pending_setup: 'info',
  trial: 'info',
  active: 'success',
  suspended: 'warning',
  cancelled: 'danger',
  archived: 'neutral',
}

const ORG_LABELS: Record<PlatformOrganizationStatus, string> = {
  pending_setup: 'Pending setup',
  trial: 'Trial',
  active: 'Active',
  suspended: 'Suspended',
  cancelled: 'Cancelled',
  archived: 'Archived',
}

export function OrganizationStatusBadge({ status }: { status: PlatformOrganizationStatus }) {
  return (
    <Badge tone={ORG_TONES[status] ?? 'neutral'}>{ORG_LABELS[status] ?? humanize(status)}</Badge>
  )
}

const SUBSCRIPTION_TONES: Record<SubscriptionStatus, Tone> = {
  trialing: 'info',
  active: 'success',
  past_due: 'warning',
  cancelled: 'danger',
  expired: 'neutral',
}

export function SubscriptionStatusBadge({ status }: { status: SubscriptionStatus }) {
  return <Badge tone={SUBSCRIPTION_TONES[status] ?? 'neutral'}>{humanize(status)}</Badge>
}

/** The order the console lists statuses in: live first, gone last. */
export const ORGANIZATION_STATUSES: PlatformOrganizationStatus[] = [
  'pending_setup',
  'trial',
  'active',
  'suspended',
  'cancelled',
  'archived',
]

export const ORGANIZATION_STATUS_LABELS = ORG_LABELS
