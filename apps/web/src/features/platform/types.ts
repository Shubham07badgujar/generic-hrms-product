/**
 * What the platform console talks about.
 *
 * DELIBERATELY NOT IN `lib/types.ts`. The console and the HR application share
 * a build and nothing else: no route, no navigation entry, no query key, and
 * no vocabulary. Organizations, plans and subscriptions are commercial objects
 * an operator reasons about; employees, payslips and candidates are customer
 * records an operator cannot reach at all. Keeping the two type files apart is
 * the cheapest way to notice when something starts crossing over — an import
 * from `@/features/platform` inside an HR screen is visible in review, where a
 * shared interface never would be.
 *
 * `FeatureCode` is the one borrowed name, because a plan's feature list and a
 * customer's own plan page have to mean the same thing by definition.
 */

import type { FeatureCode, SubscriptionStatus, UUID } from '@/lib/types'

/** Mirrors `apps.organization.models.Organization.Status`. */
export type PlatformOrganizationStatus =
  | 'pending_setup'
  | 'trial'
  | 'active'
  | 'suspended'
  | 'cancelled'
  | 'archived'

export interface PlatformSubscription {
  id: UUID
  plan_code: string
  plan_name: string
  status: SubscriptionStatus
  /** The effective limit: the override where one is set, else the plan's. */
  employee_limit: number | null
  employee_limit_override: number | null
  override_reason: string
  enabled_features: FeatureCode[]
  started_at: string
  ends_at: string | null
  cancelled_at: string | null
  read_only_grace_days: number
  features_narrowed_at: string | null
}

export interface PlatformOrganization {
  id: UUID
  name: string
  legal_name: string
  slug: string
  status: PlatformOrganizationStatus
  is_operational: boolean
  primary_email: string
  phone: string
  city: string
  state: string
  country: string
  timezone: string
  currency: string
  /** Active employees. A COUNT, and nothing that identifies one of them. */
  employee_count: number
  member_count: number
  /**
   * An administrator has never signed in, so their invitation may be resent.
   * The same test the service applies, so the button and the refusal agree.
   */
  admin_invitation_pending: boolean
  /** Null on a deployment that sells nothing — a real state, not an error. */
  subscription: PlatformSubscription | null
  /** When it entered ARCHIVED; the one-year purge clock runs from here. */
  archived_at: string | null
  /** Set once its data is purged. The row survives as a tombstone. */
  purged_at: string | null
  created_at: string
}

/**
 * The provisioning response, which is the organization plus two extra fields.
 *
 * THERE IS NO PASSWORD HERE, and there is not meant to be. The service hands
 * the temporary credential to the invitation email and to nothing else;
 * `invitation_sent` says whether that email actually went, so the console can
 * tell an operator to arrange a password out of band instead of claiming
 * credentials were sent when they were not.
 */
export interface ProvisionedOrganization extends PlatformOrganization {
  admin_email: string
  invitation_sent: boolean
}

export interface PlatformPlan {
  id: UUID
  code: string
  name: string
  description: string
  employee_limit: number | null
  disabled_features: FeatureCode[]
  enabled_features: FeatureCode[]
  storage_limit_mb: number | null
  support_level: string
  is_public: boolean
  display_order: number
  subscriber_count: number
}

export interface PlatformSummary {
  organizations_total: number
  organizations_operational: number
  by_status: Partial<Record<PlatformOrganizationStatus, number>>
}

/** A support-access grant, as the operator who asked for it sees it. */
export interface PlatformSupportGrant {
  id: string
  organization_slug: string
  organization_name: string
  requested_by_email: string
  reason: string
  status: 'requested' | 'approved' | 'denied' | 'revoked'
  decided_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
  usable: boolean
}

/**
 * What an approved grant shows: the customer's configuration, table by table,
 * from a fixed server-side list. Rows are plain records; nothing here
 * describes a person.
 */
export interface SupportConfiguration {
  organization: string
  grant: string
  expires_at: string
  tables: Record<string, Array<Record<string, unknown>>>
}

/** Who a resend reached, and whether each mail actually went. No password. */
export interface InvitationResend {
  invitations: Array<{ email: string; sent: boolean }>
}

/** What creating a customer takes. Every rule lives in the service. */
export interface ProvisionRequest {
  name: string
  admin_email: string
  slug?: string
  legal_name?: string
  admin_first_name?: string
  admin_last_name?: string
  primary_email?: string
  plan?: string
}
