/**
 * API types, mirrored from the DRF serializers.
 *
 * Hand-written rather than generated: the backend ships drf-spectacular, and
 * generating a client from `/api/schema/` is the right long-term move, but it
 * requires the schema endpoint to be exposed outside DEBUG. Until then these
 * are kept deliberately narrow — every field here exists in a serializer, and
 * nothing is invented.
 */

export type UUID = string
export type ISODate = string
export type ISODateTime = string

/* --------------------------------------------------------------- identity */

export interface Me {
  id: UUID
  email: string
  first_name: string
  last_name: string
  full_name: string
  roles: RoleCode[]
  employee_id: UUID | null
  /** Lifecycle status of the linked employee ("on_probation", "confirmed", …). */
  employee_status: string
  must_change_password: boolean
  /** True while mandatory onboarding items are outstanding — the API gates
   *  everything except onboarding, documents and identity meanwhile. */
  onboarding_pending: boolean
  /** True while the employee's own handbook acknowledgement is still open —
   *  the SPA shows the handbook right after the first password reset and
   *  requires the acknowledgement before anything else. */
  handbook_acknowledgement_pending: boolean
  last_login_at: ISODateTime | null
}

export type Scope = 'none' | 'self' | 'team' | 'department' | 'all'

export type DashboardKey = 'ceo' | 'admin' | 'department' | 'manager' | 'executive' | 'self'

/** `GET /me/permissions/` — advisory projection of the server's AccessContext. */
/**
 * A module the organization's plan may include.
 *
 * Mirrors `core.access.features.FeatureCode`. The backend holds the negative
 * form (which features a plan EXCLUDES, so that adding one does not switch it
 * off for every existing customer) and inverts it here, so on this side
 * absence means unavailable — the same truthiness rule as `grants`.
 */
export type FeatureCode =
  | 'core'
  | 'recruitment'
  | 'onboarding'
  | 'offboarding'
  | 'attendance'
  | 'attendance_biometric'
  | 'leave'
  | 'payroll'
  | 'assets'
  | 'it_accounts'
  | 'reporting'

/** Commercial state of the subscription, which is not access state. */
export type SubscriptionStatus =
  | 'trialing'
  | 'active'
  | 'past_due'
  | 'cancelled'
  | 'expired'

export interface PlanSummary {
  code: string
  name: string
  description: string
  support_level: 'community' | 'standard' | 'priority' | string
}

/**
 * What this organization is on, and how much of it is used.
 *
 * `plan` is null on a deployment that sells nothing — a self-hosted install
 * that never bought a seat count — and that is reported as unlimited rather
 * than as an error, because that is what it is.
 */
export interface MyPlan {
  plan: PlanSummary | null
  status: SubscriptionStatus | null
  features: FeatureCode[]
  employees_used: number
  /** null means no limit. */
  employee_limit: number | null
  seats_remaining: number | null
  /** Surfaced, not enforced: a cap that silently broke payroll would be worse. */
  storage_limit_mb: number | null
  trial_ends_at?: ISODateTime | null
}

/**
 * One step of the setup checklist, as the server reports it.
 *
 * `complete` is COMPUTED from the real domain tables on every read, never
 * stored: closing the browser loses nothing, and deleting the last department
 * honestly reopens that step.
 */
export interface SetupStep {
  key: string
  title: string
  required: boolean
  /** Where the step is actually done — an existing page, not a wizard form. */
  route: string
  detail: string
  complete: boolean
}

export interface SetupState {
  status: OrganizationStatus
  in_setup: boolean
  steps: SetupStep[]
  completed: number
  total: number
  /** Required steps still outstanding, by key. */
  blocking: string[]
  /** The server's verdict, not a count computed here. */
  can_finish: boolean
}

/** Lifecycle of the organization itself, which decides whether it may be used. */
export type OrganizationStatus =
  | 'pending_setup'
  | 'trial'
  | 'active'
  | 'suspended'
  | 'cancelled'
  | 'archived'
  | ''

export interface PermissionSnapshot {
  dashboard: DashboardKey
  read_only: boolean
  can_manage_users: boolean
  layers: number[]
  roles: RoleCode[]
  /** resource -> action -> scope */
  grants: Record<string, Record<string, Scope>>
  /** What this organization's plan includes. Absence means unavailable. */
  features: FeatureCode[]
  /** Carried even on a denial, so a refusal can say WHY — a suspended
   *  customer and a user without permission are otherwise the same 403. */
  organization_status: OrganizationStatus
  notice: string
}

export type RoleCode =
  | 'ceo'
  | 'admin'
  | 'medical_director'
  | 'operational_head'
  | 'hr_head'
  | 'finance_head'
  | 'senior_doctor'
  | 'operations_manager'
  | 'hr_manager'
  | 'accounts_manager'
  | 'clinic_doctor'
  | 'cre'
  | 'recruiter'
  | 'payroll_executive'
  | 'executive'
  | 'therapist'
  | 'office_boy'
  | 'employee'

/* ---------------------------------------------------------- organization */

export type DepartmentKind = 'medical' | 'operations' | 'hr' | 'finance' | 'other'

export interface Department {
  id: UUID
  name: string
  code: string
  kind: DepartmentKind
  description: string
  parent_department: UUID | null
  head_employee: UUID | null
  head_employee_name: string | null
}

export interface Designation {
  id: UUID
  title: string
  department: UUID | null
  department_name: string | null
  description: string
}

export interface Location {
  id: UUID
  name: string
  code: string
  city: string
  state: string
  pincode: string
  is_head_office: boolean
}

export interface EmployeeLevel {
  id: UUID
  name: string
  code: string
  layer: number
  rank: number
}

export interface Role {
  id: UUID
  code: RoleCode
  name: string
  layer: number
  /** Seeded canonical role: code, layer and flags are immutable. */
  is_system: boolean
  description: string
  is_read_only: boolean
  can_manage_users: boolean
  requires_employee: boolean
  is_grantable: boolean
  department_kind: string
  dashboard_key: DashboardKey
}

/* ---------------------------------------------------------------- people */

export type EmployeeStatus =
  | 'onboarding'
  | 'on_probation'
  | 'active'
  | 'confirmed'
  | 'on_leave'
  | 'on_notice'
  | 'resigned'
  | 'terminated'
  | 'exited'

export interface EmployeeListItem {
  id: UUID
  employee_code: string
  full_name: string
  work_email: string
  department: UUID | null
  department_name: string | null
  designation: UUID | null
  designation_title: string
  reporting_manager: UUID | null
  reporting_manager_name: string
  employment_type: string
  date_of_joining: ISODate
  status: EmployeeStatus
  roles: RoleCode[]
}

/**
 * The full profile.
 *
 * `pan`, `aadhaar` and `bank_account_number` arrive MASKED — the serializer
 * sources them from `*_masked` properties. There is no unmasked variant on
 * this endpoint by design, so the UI can render them without any redaction
 * logic of its own.
 */
export interface EmployeeDetail extends EmployeeListItem {
  first_name: string
  middle_name: string
  last_name: string
  personal_email: string
  phone: string
  date_of_birth: ISODate | null
  gender: string
  location: UUID | null
  level: UUID | null
  team: UUID | null
  probation_start_date: ISODate | null
  probation_end_date: ISODate | null
  confirmation_date: ISODate | null
  probation_status: ProbationStatus
  date_of_exit: ISODate | null
  notice_period_days: number | null
  pan: string
  aadhaar: string
  bank_account_number: string
  bank_ifsc: string
  bank_name: string
  uan: string
  esic_number: string
}

export interface EmployeeCreatePayload {
  first_name: string
  last_name?: string
  /** Company Email — the one and only HRMS login identifier. */
  email: string
  /** Personal Email — where the credential email is delivered. Required. */
  personal_email: string
  phone?: string
  role_code: RoleCode
  department_id: UUID
  designation_id?: UUID
  location_id?: UUID
  reporting_manager_id?: UUID
  date_of_joining: ISODate
  employment_type?: string
  /** Annual CTC agreed at hire — informational; payroll owns actual pay. */
  annual_ctc?: string
}

export interface EmployeeCreateResult {
  /** Whether the welcome email actually went. null = not attempted. */
  welcome_email_sent?: boolean | null
  employee: EmployeeDetail
  user: { id: UUID; email: string }
  role: RoleCode
  temporary_password: string | null
}

/* -------------------------------------------------------------- workflow */

export type StageKind =
  | 'application'
  | 'hr_verification'
  | 'interview'
  | 'department_decision'
  | 'hr_final_decision'
  | 'offer'
  | 'onboarding'
  | 'terminal'

export type Decision =
  | 'pass'
  | 'verify'
  | 'request_info'
  | 'screen_out'
  | 'recommend_select'
  | 'recommend_reject'
  | 'select'
  | 'reject'
  | 'offer_accepted'
  | 'offer_declined'
  | 'withdraw'

export type FeedbackFieldKind = 'rating_1_5' | 'boolean' | 'text' | 'choice' | 'score_0_100'

export interface FeedbackField {
  id: UUID
  key: string
  label: string
  kind: FeedbackFieldKind
  choices: string[] | null
  order: number
  is_required: boolean
}

export interface FeedbackForm {
  id: UUID
  name: string
  description: string
  fields_: FeedbackField[]
}

export interface StageTransition {
  id: UUID
  on_decision: Decision
  to_stage: UUID
  to_stage_name: string
  to_stage_order: number
}

export interface WorkflowStage {
  id: UUID
  name: string
  order: number
  kind: StageKind
  responsible_role: UUID | null
  responsible_role_code: RoleCode | null
  allowed_decisions: Decision[]
  requires_interview: boolean
  requires_feedback: boolean
  feedback_form: UUID | null
  is_final_hr_decision: boolean
  is_terminal: boolean
  is_won: boolean
  transitions: StageTransition[]
}

export interface HiringWorkflowSummary {
  id: UUID
  name: string
  description: string
  department_kind: string
  is_published: boolean
  stage_count: number
}

export interface HiringWorkflow extends Omit<HiringWorkflowSummary, 'stage_count'> {
  version: number
  stages: WorkflowStage[]
}

/* ----------------------------------------------------------- recruitment */

export type JobStatus = 'draft' | 'published' | 'on_hold' | 'closed' | 'filled'

export interface JobOpening {
  id: UUID
  title: string
  workflow: UUID
  workflow_name: string
  department: UUID
  department_name: string
  designation: UUID | null
  location: UUID | null
  level: UUID | null
  target_role: UUID
  target_role_code: RoleCode
  description: string
  requirements: string
  openings_count: number
  employment_type: string
  age_limit: number | null
  gender_preference: 'any' | 'male' | 'female'
  salary: string
  status: JobStatus
  recruiter: UUID | null
  hiring_manager: UUID | null
  published_at: ISODateTime | null
  closed_at: ISODateTime | null
  application_count: number
  created_at: ISODateTime
  /** The public application link for this job — /apply/<token>. */
  application_token: string
  application_url: string
  accepts_applications: boolean
  /** Catalogue keys and/or extra questions; empty = the whole catalogue. */
  application_fields: ApplicationFieldConfig[]
  resolved_application_fields: ApplicationFieldSpec[]
  external_form_provider: 'hosted' | 'google_forms' | string
  external_form_id: string
  external_form_url: string
  external_form_synced_at: ISODateTime | null
  external_form_error: string
}

export type ApplicationFieldType =
  | 'text' | 'email' | 'phone' | 'number' | 'date' | 'url' | 'select' | 'textarea' | 'file'

export interface ApplicationFieldSpec {
  key: string
  label: string
  type: ApplicationFieldType
  target: 'candidate' | 'profile' | 'answers'
  required: boolean
  options: string[]
  help_text: string
}

export type ApplicationFieldConfig =
  | string
  | { key: string; label: string; type?: ApplicationFieldType; required?: boolean; options?: string[]; help_text?: string }

/** What the anonymous /public/apply/<token>/ endpoint returns. */
export interface PublicJobPosting {
  title: string
  department: string
  location: string
  employment_type: string
  description: string
  requirements: string
  accepts_applications: boolean
  fields: ApplicationFieldSpec[]
  consent_text: string
}

export type CandidateNotificationKind =
  | 'application_received' | 'hr_verification_passed' | 'hr_verification_rejected'
  | 'interview_scheduled' | 'interview_rescheduled' | 'interview_cancelled'
  | 'interview_selected' | 'interview_not_selected' | 'interview_on_hold'
  | 'department_decision' | 'final_selection' | 'final_rejection'
  | 'offer_sent' | 'offer_accepted' | 'offer_declined'

export type CandidateNotificationStatus = 'pending' | 'sent' | 'failed' | 'skipped'

export interface CandidateNotification {
  id: UUID
  application: UUID
  candidate: UUID
  job_opening: UUID
  kind: CandidateNotificationKind
  kind_display: string
  recipient_email: string
  subject: string
  body_text: string
  status: CandidateNotificationStatus
  status_display: string
  attempts: number
  last_attempt_at: ISODateTime | null
  sent_at: ISODateTime | null
  error: string
  triggered_by_email: string
  created_at: ISODateTime
}

export interface Candidate {
  id: UUID
  first_name: string
  last_name: string
  full_name: string
  email: string
  phone: string
  current_employer: string
  total_experience_years: string | null
  expected_ctc: string | null
  notice_period_days: number | null
  /** Whether a résumé file is on record. The file itself comes only through
   * the authorised GET /candidates/<id>/resume/ download. */
  resume: boolean
  has_resume: boolean
  resume_name: string
  source: string
  /** Descriptive attributes from the application form (city, qualification, skills…). */
  profile: Record<string, string | number>
  /** This candidate's applications the caller may see — job, status, stage. */
  applications: CandidateApplicationSummary[]
  consent_given: boolean
  consent_at: ISODateTime | null
  final_decision_at: ISODateTime | null
  retention_until: ISODate | null
  created_at: ISODateTime
}

export interface CandidateApplicationSummary {
  id: UUID
  job_opening: UUID
  job_title: string
  department_name: string
  status: ApplicationStatus
  stage_name: string
  applied_at: ISODateTime
}

export type ApplicationStatus =
  | 'active'
  | 'selected'
  | 'offer_sent'
  | 'offer_accepted'
  | 'offer_declined'
  | 'hired'
  | 'rejected'
  | 'withdrawn'

export interface Application {
  id: UUID
  candidate: UUID
  candidate_name: string
  job_opening: UUID
  job_title: string
  department_name: string
  current_stage: UUID
  stage_name: string
  stage_kind: StageKind
  allowed_decisions: Decision[]
  status: ApplicationStatus
  is_verified: boolean
  applied_at: ISODateTime
  /** Answers to the job's own extra questions, from the application form. */
  form_answers: Record<string, string | number | string[]>
  /** The role the workflow says acts at the current stage; null for automatic stages. */
  stage_responsible_role: RoleCode | null
  stage_responsible_role_name: string | null
  candidate_email: string
  /** The open slot invite at the current stage, if any. */
  slot_invite: {
    id: UUID
    status: 'pending' | 'selected'
    /** The windows HR configured for this round. */
    options: { start: string; end: string }[]
    selected_slot: { start: string; end: string } | null
    selected_at: ISODateTime | null
    expires_at: ISODateTime
    /** The live booking link, for staff to copy or re-send. */
    selection_url: string
    /** Invites this round has needed — 1 is normal, more means rebooked. */
    invite_count: number
  } | null
  /** Whether an interview exists for the current stage — Pass/Reject opens after it. */
  stage_interview_scheduled: boolean
  /** The current interview round's feedback is in — decisions may open. */
  stage_feedback_submitted: boolean
  /** Prefill for the onboarding form. Present only once the offer is accepted. */
  conversion_defaults: {
    first_name: string
    last_name: string
    email: string
    personal_email: string
    phone: string
    department: UUID | null
    designation: UUID | null
    location: UUID | null
    reporting_manager: UUID | null
    date_of_joining: ISODate | null
  } | null
}

export type EventKind =
  | 'applied'
  | 'stage_changed'
  | 'verified'
  | 'info_requested'
  | 'interview_scheduled'
  | 'interview_completed'
  | 'feedback_submitted'
  | 'recommendation'
  | 'hr_decision'
  | 'rejected'
  | 'override'
  | 'offer_created'
  | 'offer_sent'
  | 'offer_responded'
  | 'converted'

export interface ApplicationEvent {
  id: UUID
  kind: EventKind
  from_stage_name: string | null
  to_stage_name: string | null
  decision: string
  actor_label: string
  note: string
  detail: Record<string, unknown>
  created_at: ISODateTime
}

export interface CandidateRejection {
  id: UUID
  rejected_by: UUID
  rejected_by_email: string
  rejected_at: ISODateTime
  rejection_stage: UUID
  stage_name: string
  reason: string
  department_recommendation: UUID | null
  is_overridden: boolean
}

export interface DecisionOverride {
  id: UUID
  overridden_by: UUID
  overridden_by_email: string
  overridden_at: ISODateTime
  previous_status: ApplicationStatus
  new_status: ApplicationStatus
  /** An override that reopens a candidate MOVES them; the stage pair records where. */
  previous_stage: UUID | null
  previous_stage_name: string | null
  new_stage: UUID | null
  new_stage_name: string | null
  reason: string
}

/** `GET /applications/{id}/history/` */
export interface ApplicationHistory {
  candidate: string
  job: string
  verified: boolean
  status: ApplicationStatus
  current_stage: string
  decisions: Array<{
    stage: string
    decision: Decision
    by: string
    at: ISODateTime
    rationale: string
  }>
  interviews: Array<{
    stage: string
    interviewer: string
    scheduled_at: ISODateTime
    status: InterviewStatus
    recommendation: Recommendation | null
    rating: number | null
    strengths: string
    concerns: string
  }>
  events: ApplicationEvent[]
  rejection: CandidateRejection | null
  overrides: DecisionOverride[]
}

export interface CandidateHistory {
  candidate: Candidate
  applications: ApplicationHistory[]
}

export type InterviewStatus = 'scheduled' | 'rescheduled' | 'completed' | 'cancelled' | 'no_show'
export type Recommendation = 'strong_hire' | 'hire' | 'hold' | 'no hire'

export interface Interview {
  id: UUID
  application: UUID
  candidate_name: string
  stage: UUID
  stage_name: string
  /** The assessment form this interview must be reported on; null if none. */
  stage_feedback_form: UUID | null
  interviewer: UUID
  interviewer_name: string
  scheduled_at: ISODateTime
  scheduled_end: ISODateTime
  duration_minutes: number
  mode: string
  location_or_link: string
  status: InterviewStatus
  feedback_submitted: boolean
  /** Google Calendar mirror — HRMS stays the source of truth. */
  calendar_event_id: string
  calendar_sync_status: '' | 'synced' | 'failed' | 'cancelled'
  calendar_error: string
}

export interface ConflictCheck {
  conflict: boolean
  interview: Interview | null
}

export interface InterviewFeedback {
  id: UUID
  interview: UUID
  stage_name: string
  form: UUID
  submitted_by: UUID
  submitted_by_name: string
  submitted_at: ISODateTime
  answers: Record<string, string | number | boolean>
  strengths: string
  concerns: string
  recommendation: Recommendation
  overall_rating: number | null
}

export type OfferStatus = 'draft' | 'sent' | 'accepted' | 'declined' | 'withdrawn'

export interface Offer {
  id: UUID
  application: UUID
  candidate_name: string
  offered_ctc: string
  joining_date: ISODate
  valid_until: ISODate | null
  designation: UUID | null
  level: UUID | null
  reporting_manager: UUID | null
  status: OfferStatus
  sent_at: ISODateTime | null
  responded_at: ISODateTime | null
  created_at: ISODateTime
}

/** `POST /applications/{id}/{advance,recommend,select,reject}/` */
export interface DecisionResult {
  application: Application
  from_stage: string
  to_stage: string | null
  decision: Decision
}

/** `POST /applications/{id}/convert/` */
export interface ConversionResult {
  employee_id: UUID
  employee_code: string
  user_id: UUID
  role: RoleCode
  temporary_password: string | null
}

/* ------------------------------------------------------------- lifecycle */

export type ProbationStatus =
  | 'not_applicable'
  | 'active'
  | 'due'
  | 'confirmed'
  | 'extended'
  | 'terminated'

export type ProbationDecisionValue = 'pending' | 'confirm' | 'extend' | 'terminate'

export type VerificationStatus = 'pending' | 'verified' | 'rejected' | 'expired'

export type DocumentCategory =
  | 'identity'
  | 'address'
  | 'education'
  | 'experience'
  | 'employment'
  | 'compliance'
  | 'joining'
  | 'company_letter'
  | 'other'

export interface DocumentType {
  id: UUID
  name: string
  code: string
  category: DocumentCategory
  description: string
  is_mandatory: boolean
  requires_expiry: boolean
  order: number
}

/**
 * A document's METADATA.
 *
 * There is deliberately no file URL. The bytes come from
 * `/employee-documents/{id}/download/`, which re-checks scope on every
 * request, so a link cannot outlive the permission that produced it.
 */
export interface EmployeeDocument {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string | null
  document_type: UUID
  document_type_name: string
  category: DocumentCategory
  original_filename: string
  content_type: string
  size_bytes: number
  has_file: boolean
  uploaded_by: UUID | null
  uploaded_by_email: string | null
  uploaded_at: ISODateTime
  /** Somebody filed this into another person's record. Computed server-side. */
  filed_by_someone_else: boolean
  status: VerificationStatus
  verified_by: UUID | null
  verified_by_email: string | null
  verified_at: ISODateTime | null
  rejected_by: UUID | null
  rejected_by_email: string | null
  rejected_at: ISODateTime | null
  rejection_reason: string
  issue_date: ISODate | null
  expires_on: ISODate | null
  notes: string
}

export interface ProbationReview {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string | null
  probation_end_date: ISODate
  reviewer: UUID | null
  reviewer_name: string | null
  reviewed_at: ISODateTime | null
  performance_rating: number | null
  reliability_rating: number | null
  role_specific_rating: number | null
  strengths: string
  areas_for_improvement: string
  recommendation: ProbationDecisionValue
  reviewer_notes: string
  decision: ProbationDecisionValue
  decided_by: UUID | null
  decided_by_email: string | null
  decided_at: ISODateTime | null
  rationale: string
  extended_to: ISODate | null
  confirmation_letter: UUID | null
  is_decided: boolean
  notified_30d: boolean
  notified_7d: boolean
  notified_overdue_on: ISODate | null
}

export interface ProbationDue {
  id: UUID
  employee_code: string
  full_name: string
  department_name: string | null
  probation_end_date: ISODate
  probation_status: ProbationStatus
}

export interface ProbationBuckets {
  t_minus_30: ProbationDue[]
  t_minus_7: ProbationDue[]
  overdue: ProbationDue[]
}

export type OnboardingItemKind =
  | 'document'
  | 'task'
  | 'account'
  | 'asset'
  | 'acknowledgement'

export type OnboardingItemOwner =
  | 'hr'
  | 'manager'
  | 'employee'
  | 'department_head'
  | 'admin'

export type OnboardingItemStatus =
  | 'pending'
  | 'in_progress'
  | 'submitted'
  | 'completed'
  | 'waived'
  | 'blocked'

export interface OnboardingItem {
  id: UUID
  title: string
  description: string
  kind: OnboardingItemKind
  owner: OnboardingItemOwner
  assigned_to: UUID | null
  assigned_to_name: string | null
  document_type: UUID | null
  document_type_name: string | null
  document: UUID | null
  is_mandatory: boolean
  due_date: ISODate | null
  order: number
  status: OnboardingItemStatus
  completed_at: ISODateTime | null
  completed_by: UUID | null
  completed_by_email: string | null
  notes: string
  is_overdue: boolean
  is_done: boolean
}

export interface EmployeeOnboarding {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string | null
  template: UUID | null
  template_name: string
  joining_date: ISODate
  status: 'in_progress' | 'completed' | 'cancelled'
  completed_at: ISODateTime | null
  notes: string
  items: OnboardingItem[]
  completed_count: number
  total_count: number
  outstanding_mandatory_count: number
}

export type LetterTypeValue =
  | 'offer'
  | 'appointment'
  | 'joining'
  | 'confirmation'
  | 'extension'
  | 'experience'
  | 'relieving'
  | 'warning'
  | 'other'

export interface LetterTemplate {
  id: UUID
  name: string
  letter_type: LetterTypeValue
  subject: string
  version: number
  is_default: boolean
}

export interface EmployeeLetter {
  id: UUID
  employee: UUID
  employee_name: string
  letter_type: LetterTypeValue
  template: UUID | null
  template_name: string
  template_version: number
  subject: string
  status: 'draft' | 'issued' | 'acknowledged' | 'revoked'
  generated_by: UUID | null
  generated_by_email: string | null
  generated_at: ISODateTime
  issued_at: ISODateTime | null
  acknowledged_at: ISODateTime | null
  has_pdf: boolean
}

export type AccountStatusValue =
  | 'requested'
  | 'provisioning'
  | 'active'
  | 'suspended'
  | 'deprovisioned'

/**
 * A company mailbox, as METADATA.
 *
 * There is no credential field here because the backend has no column for one.
 * The password is created in the provider's console and reaches the employee
 * through a channel this system is not part of.
 */
export interface CompanyEmailAccount {
  id: UUID
  employee: UUID
  employee_name: string
  email_address: string
  provider: string
  external_account_id: string
  status: AccountStatusValue
  requested_by: UUID | null
  requested_by_email: string | null
  requested_at: ISODateTime | null
  provisioned_by: UUID | null
  provisioned_by_email: string | null
  provisioned_at: ISODateTime | null
  suspended_at: ISODateTime | null
  deprovisioned_at: ISODateTime | null
  notes: string
}

export type AssetStatusValue =
  | 'available'
  | 'allocated'
  | 'in_maintenance'
  | 'retired'
  | 'lost'

export type AssetConditionValue = 'new' | 'good' | 'fair' | 'damaged' | 'unusable'

export type AllocationStatusValue = 'active' | 'returned' | 'overdue' | 'written_off'

export interface AssetCategory {
  id: UUID
  name: string
  code: string
  description: string
  requires_serial: boolean
  is_returnable: boolean
}

export interface Asset {
  id: UUID
  asset_tag: string
  category: UUID
  category_name: string
  name: string
  serial_number: string
  make: string
  model: string
  purchase_date: ISODate | null
  purchase_cost: string | null
  warranty_expires_on: ISODate | null
  vendor: string
  location: UUID | null
  location_name: string | null
  status: AssetStatusValue
  condition: AssetConditionValue
  notes: string
  held_by_name: string | null
}

export interface AssetAllocation {
  id: UUID
  asset: UUID
  asset_tag: string
  asset_name: string
  asset_category: string
  employee: UUID
  employee_name: string
  allocated_at: ISODateTime
  allocated_by: UUID | null
  allocated_by_email: string | null
  condition_at_allocation: AssetConditionValue
  allocation_notes: string
  expected_return_date: ISODate | null
  returned_at: ISODateTime | null
  received_by: UUID | null
  received_by_email: string | null
  condition_at_return: AssetConditionValue | ''
  return_notes: string
  status: AllocationStatusValue
  write_off_reason: string
  is_open: boolean
}

/**
 * `GET /employees/{id}/profile/`
 *
 * Every section is OPTIONAL because the backend omits any the caller may not
 * read. Absent means "not permitted", which is a different statement from an
 * empty array — and the UI is written to say so rather than render a
 * convincing-looking nothing.
 */
export interface EmployeeProfile {
  employee: EmployeeDetail
  allowed_status_transitions: EmployeeStatus[]
  documents?: EmployeeDocument[]
  onboarding?: EmployeeOnboarding | null
  probation_reviews?: ProbationReview[]
  asset_allocations?: AssetAllocation[]
  company_account?: CompanyEmailAccount | null
  letters?: EmployeeLetter[]
  recruitment_source?: {
    candidate_id: UUID
    candidate_name: string
    source: string
    applied_on: ISODateTime
  }
}

/* ------------------------------------------------------------ offboarding */

export type ExitTypeValue =
  | 'resignation'
  | 'termination'
  | 'end_of_contract'
  | 'retirement'
  | 'abandonment'

export type ExitStageValue =
  | 'initiated'
  | 'notice_period'
  | 'clearance'
  | 'pending_approval'
  | 'approved'
  | 'completed'
  | 'cancelled'

export type ResignationStatusValue = 'submitted' | 'approved' | 'rejected' | 'withdrawn'

export type ResignationReasonValue =
  | 'better_opportunity'
  | 'compensation'
  | 'relocation'
  | 'higher_studies'
  | 'personal'
  | 'health'
  | 'work_environment'
  | 'career_change'
  | 'other'

export interface ResignationRequest {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string | null
  resignation_date: ISODate
  requested_last_working_date: ISODate
  reason: ResignationReasonValue
  employee_comments: string
  submitted_at: ISODateTime
  submitted_by: UUID | null
  status: ResignationStatusValue
  reviewed_by: UUID | null
  reviewed_by_email: string | null
  reviewed_at: ISODateTime | null
  review_notes: string
  approved_last_working_date: ISODate | null
  is_open: boolean
}

export type ClearanceCategoryValue = 'hr' | 'department' | 'it' | 'finance' | 'assets'

export type ClearanceOwnerValue =
  | 'hr'
  | 'department'
  | 'manager'
  | 'it'
  | 'finance'
  | 'employee'

export type ClearanceStatusValue =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'waived'
  | 'blocked'

export interface ExitClearanceItem {
  id: UUID
  exit_workflow: UUID
  title: string
  description: string
  category: ClearanceCategoryValue
  owner: ClearanceOwnerValue
  assigned_to: UUID | null
  assigned_to_name: string | null
  is_required: boolean
  requires_evidence: boolean
  due_date: ISODate | null
  order: number
  status: ClearanceStatusValue
  has_evidence: boolean
  completed_at: ISODateTime | null
  completed_by: UUID | null
  completed_by_email: string | null
  notes: string
  is_done: boolean
  is_overdue: boolean
}

export type SettlementStatusValue = 'draft' | 'in_review' | 'cleared' | 'paid' | 'disputed'

/**
 * The settlement ledger.
 *
 * Amounts are decimal STRINGS, as the API sends them — parsing them into
 * JavaScript numbers would introduce float drift into money.
 */
export interface FinalSettlement {
  id: UUID
  exit_workflow: UUID
  final_working_date: ISODate | null
  pending_salary: string
  leave_encashment: string
  bonus_or_incentive: string
  other_earnings: string
  outstanding_advances: string
  notice_shortfall_recovery: string
  asset_recovery: string
  other_deductions: string
  notes: string
  status: SettlementStatusValue
  prepared_by: UUID | null
  cleared_by: UUID | null
  cleared_by_email: string | null
  cleared_at: ISODateTime | null
  paid_at: ISODateTime | null
  gross_earnings: string
  total_deductions: string
  net_payable: string
}

export type RehireEligibilityValue =
  | 'eligible'
  | 'eligible_with_notes'
  | 'not_eligible'
  | 'undecided'

export interface ExitInterview {
  id: UUID
  exit_workflow: UUID
  conducted_by: UUID | null
  conducted_by_email: string | null
  conducted_at: ISODateTime | null
  primary_reason: ResignationReasonValue
  employee_feedback: string
  manager_feedback: string
  workplace_feedback: string
  improvement_suggestions: string
  would_recommend_employer: boolean | null
  rehire_eligibility: RehireEligibilityValue
  hr_notes: string
  is_conducted: boolean
}

/**
 * One reason an exit cannot complete.
 *
 * Computed by the SERVER (`exit_blockers()`), which is also what guards
 * approval and completion. The UI renders this list; it never derives its own,
 * so what the user is shown and what the server enforces cannot disagree.
 */
export interface ExitBlocker {
  /** Unique per blocker — `gate` alone collides, since one gate can raise both
   *  an outstanding-clearance blocker and an underlying-state blocker. */
  id: string
  gate: string
  label: string
  detail: string
  items: string[]
}

export interface ExitWorkflowSummary {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string | null
  employee_status: EmployeeStatus
  exit_type: ExitTypeValue
  stage: ExitStageValue
  notice_start_date: ISODate | null
  notice_days: number
  expected_last_working_date: ISODate
  actual_last_working_date: ISODate | null
  notice_waived: boolean
  early_release_approved: boolean
  initiated_at: ISODateTime
  approved_at: ISODateTime | null
  completed_at: ISODateTime | null
  completed_items: number
  total_items: number
}

export interface ExitWorkflowDetail extends ExitWorkflowSummary {
  resignation: ResignationRequest | null
  reason: string
  notice_waived_by: UUID | null
  notice_waived_at: ISODateTime | null
  notice_waiver_reason: string
  early_release_by: UUID | null
  early_release_reason: string
  approved_by: UUID | null
  approval_notes: string
  cancelled_reason: string
  clearance_items: ExitClearanceItem[]
  settlement: FinalSettlement | null
  interview: ExitInterview | null
  blockers: ExitBlocker[]
  can_complete: boolean
  unreturned_assets: Array<{
    allocation_id: UUID
    asset_tag: string
    asset_name: string
    category: string
  }>
}

/* ============================================================== payroll */

export type PayrollRunStatusValue =
  | 'draft' | 'processing' | 'review' | 'approved' | 'paid' | 'reversed'

export type RunTypeValue = 'regular' | 'off_cycle' | 'supplementary'

export interface PayrollRunTotals {
  employee_count?: number
  gross_earnings?: string
  total_deductions?: string
  net_pay?: string
  employer_contributions?: string
  skipped?: number
}

export interface PayrollRunSummary {
  id: UUID
  period_month: number
  period_year: number
  financial_year: string
  location: UUID | null
  location_code: string | null
  run_type: RunTypeValue
  sequence: number
  status: PayrollRunStatusValue
  locked: boolean
  totals: PayrollRunTotals
  notes: string
  approved_at: string | null
  paid_at: string | null
  reversed_at: string | null
  reversal_reason: string
  payslip_count: number
  created_at: string
}

/**
 * Why a run cannot be approved. Computed by the SERVER, from the same function
 * that guards the action — the UI renders this list rather than deriving its
 * own, so a disabled button and a refused request can never disagree.
 */
export interface PayrollBlocker {
  id: string
  gate: 'statutory' | 'payslips' | 'segregation' | string
  detail: string
}

export interface RuleSetRef {
  rule_set_id: string
  rule_version: string
  checksum: string
  effective_from: string
  jurisdiction: string
  regime: string
  verification_status: string
}

export interface PayrollRunDetail extends PayrollRunSummary {
  rule_sets_used: Record<string, RuleSetRef>
  blockers: PayrollBlocker[]
  can_approve: boolean
  payslips: PayslipSummary[]
}

export interface PayslipSummary {
  id: UUID
  payroll_run: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  period_month: number
  period_year: number
  run_status: PayrollRunStatusValue
  paid_days: string
  lop_days: string
  gross_earnings: string
  total_deductions: string
  employer_contributions: string
  net_pay: string
  state: string
  /** The Finance Head's correction window, answered by the server. */
  deletable_until: string
  within_delete_window: boolean
}

export interface PayslipLine {
  id: UUID
  label: string
  component_type: string
  amount: string
  is_employer_side: boolean
  display_order: number
}

export interface StatutoryContribution {
  id: UUID
  kind: 'pf' | 'esi' | 'pt' | 'tds' | 'gratuity'
  employee_amount: string
  employer_amount: string
  base_wage: string
  state: string
  applied: boolean
  exemption_reason: string
}

export interface PayslipDetail extends PayslipSummary {
  lines: PayslipLine[]
  statutory_contributions: StatutoryContribution[]
  warnings: string[]
}

export interface SalaryComponent {
  id: UUID
  code: string
  name: string
  component_type: string
  calc_type: 'fixed' | 'percent_of'
  percent_of_code: string
  is_taxable: boolean
  is_part_of_ctc: boolean
  is_wage: boolean
  rounding: string
  display_order: number
}

export interface SalaryStructureLine {
  component: UUID
  component_code: string
  component_name: string
  is_wage: boolean
  value: string
  monthly_amount: string
}

export interface SalaryStructure {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  ctc_annual: string
  valid_from: string
  valid_to: string | null
  revision_reason: string
  lines: SalaryStructureLine[]
  /** Statutory enrolment, decided by HR Head / Finance Head per employee. */
  pf_applicable: boolean
  esi_applicable: boolean
  pt_applicable: boolean
  tds_applicable: boolean
  gratuity_applicable: boolean
  monthly_gross: string
  monthly_wage: string
  wage_share: number
}

export interface InvestmentDeclaration {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  financial_year: string
  regime: '' | 'old' | 'new'
  declarations: Record<string, string>
  status: 'draft' | 'submitted' | 'verified' | 'rejected'
  verified_at: string | null
  review_note: string
}

export interface StatutoryRuleSet {
  id: UUID
  statute: string
  jurisdiction: string
  regime: string
  financial_year: string
  effective_from: string
  effective_to: string | null
  rule_version: string
  parameters: Record<string, unknown>
  source_citation: string
  source_url: string
  retrieved_on: string | null
  assumptions: string[]
  verification_status: 'draft' | 'pending' | 'verified' | 'rejected' | 'superseded'
  verified_by_email: string | null
  verified_at: string | null
  verification_note: string
  rejection_reason: string
  checksum: string
  is_usable_for_payroll: boolean
  is_tampered: boolean
  was_self_verified: boolean
}

export interface PackagePeriod {
  id: UUID
  order: number
  label: string
  start_date: ISODate
  end_date: ISODate
  amount: string
  months: number
  monthly_amount: string
}

export type DeferralStatusValue =
  | 'pending' | 'eligible' | 'approved' | 'paid'
  | 'rejected' | 'on_hold' | 'cancelled'

export interface PackageDeferral {
  id: UUID
  label: string
  amount: string
  condition_type: 'after_months' | 'on_date' | 'bond_completion' | 'manual' | 'other'
  condition_months: number | null
  eligible_on: ISODate | null
  condition_note: string
  status: DeferralStatusValue
  effective_status: DeferralStatusValue
  decided_by_email: string | null
  decided_at: ISODateTime | null
  decision_reason: string
  released_in_adjustment: UUID | null
}

export interface PackageSummary {
  paid_so_far: string
  deferred_total: string
  released_total: string
  remaining_deferred: string
  next_release_date: ISODate | null
  allocation_gap: string
}

export interface EmployeePackage {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  total_amount: string
  package_type: 'monthly_plus_deferred' | 'year_wise' | 'period_wise' | 'milestone'
  start_date: ISODate
  end_date: ISODate
  status: 'draft' | 'active' | 'completed' | 'on_hold' | 'cancelled'
  /** Internal HR/Finance notes — absent entirely on an employee's own view. */
  notes?: string
  supersedes: UUID | null
  bond_start_date: ISODate | null
  bond_end_date: ISODate | null
  bond_required_months: number | null
  activated_at: ISODateTime | null
  periods: PackagePeriod[]
  deferrals: PackageDeferral[]
  summary: PackageSummary
}

export interface MyPayroll {
  payslips: PayslipSummary[]
  declaration: InvestmentDeclaration | null
  structure: SalaryStructure | null
  package: EmployeePackage | null
}

export type AdjustmentKindValue =
  | 'bonus' | 'incentive' | 'arrear' | 'advance_recovery'
  | 'loan_recovery' | 'reimbursement' | 'other_earning' | 'other_deduction'

export interface PayrollAdjustment {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  kind: AdjustmentKindValue
  label: string
  amount: string
  period_month: number
  period_year: number
  is_taxable: boolean
  is_employer_side: boolean
  status: 'draft' | 'approved' | 'applied' | 'rejected'
  approved_at: string | null
  source_ref: string
  notes: string
}

/* ========================================================= BI / reporting */

export interface MetricSpec {
  key: string
  label: string
  description: string
  family: string
  unit: 'count' | 'percent' | 'currency' | 'days' | string
  shape: 'scalar' | 'series' | 'breakdown'
  groupings: string[]
  supports_range: boolean
}

export interface MetricCatalog {
  metrics: MetricSpec[]
  families: string[]
}

export interface MetricPoint {
  key: string
  label: string
  value: number | string
  context: Record<string, number | string>
}

export interface MetricResult {
  key: string
  label: string
  unit: string
  shape: 'scalar' | 'series' | 'breakdown'
  /** Server's description of the breadth returned — never inferred client-side. */
  scope_label: string
  generated_at: string
  params: { start: string; end: string; group_by: string }
  points: MetricPoint[]
}

/* ========================================================== notifications */

export type NotificationPriority = 'low' | 'normal' | 'high' | 'critical'

export interface NotificationItem {
  id: UUID
  kind: string
  priority: NotificationPriority
  title: string
  body: string
  link_url: string
  entity_type: string
  entity_id: string
  is_read: boolean
  read_at: string | null
  created_at: string
}

export interface NotificationPreferenceRow {
  kind: string
  label: string
  in_app: boolean
  email: boolean
  /** False when never configured — the row is showing the default, not a choice. */
  configured: boolean
}

/* ================================================================== audit */

export interface AuditEntry {
  id: number
  occurred_at: string
  actor: UUID | null
  actor_email: string
  actor_name: string
  action: string
  action_label: string
  resource: string
  entity_type: string
  entity_id: string
  entity_label: string
  subject_employee: UUID | null
  subject_name: string | null
  subject_code: string | null
  subject_department: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  reason: string
  ip: string | null
  request_id: string | null
  /** Server's classification — overrides, reversals, credential and PII access. */
  is_sensitive: boolean
}

export interface AuditOptions {
  actions: Array<{ value: string; label: string; sensitive: boolean }>
  resources: string[]
  entity_types: string[]
}

/* ------------------------------------------------------- candidate import */

/** Mirrors apps/imports/models.py RowStatus. */
export type ImportRowStatus =
  | 'pending'
  | 'valid'
  | 'invalid'
  | 'needs_review'
  | 'duplicate_in_file'
  | 'created'
  | 'matched_updated'
  | 'matched_skipped'
  | 'application_exists'
  | 'failed'

/** Mirrors apps/imports/models.py BatchStatus. */
export type ImportBatchStatus =
  | 'parsed'
  | 'committing'
  | 'completed'
  | 'partial'
  | 'discarded'

/** Which key identified the candidate, strongest first. */
export type ImportMatchRule = 'external_id' | 'email' | 'phone' | 'none' | ''

/**
 * A parse or commit problem, as the backend reports it.
 *
 * Codes and row numbers only — never the offending cell value. The backend
 * builds these deliberately PII-free because the same object is logged,
 * audited and sent to Sentry; the frontend must not reintroduce the value.
 */
export interface ImportIssue {
  row: number
  code: string
}

export interface ImportRow {
  id: UUID
  row_number: number
  /** Every cell of the original spreadsheet row, keyed by its header. */
  raw: Record<string, string>
  first_name: string
  last_name: string
  email: string | null
  phone: string
  external_id: string
  current_employer: string
  total_experience_years: string | null
  expected_ctc: string | null
  notice_period_days: number | null
  status: ImportRowStatus
  match_rule: ImportMatchRule
  matched_candidate: UUID | null
  duplicate_of_row: number | null
  errors: ImportIssue[]
  warnings: ImportIssue[]
}

export interface ImportBatch {
  id: UUID
  platform: string
  platform_label: string
  job_opening: UUID
  job_title: string
  original_filename: string
  file_sha256: string
  /** canonical field -> the header it came from. Column NAMES only. */
  column_mapping: Record<string, string>
  detected_headers: string[]
  unmapped_headers: string[]
  status: ImportBatchStatus
  rows_total: number
  rows_created: number
  rows_updated: number
  rows_duplicate: number
  rows_review: number
  rows_failed: number
  legal_basis: string
  attested_at: string | null
  committed_at: string | null
  created_at: string
  rows: ImportRow[]
}

/**
 * What a platform can do, and — where it cannot — why.
 *
 * `unavailable_reason` is shown verbatim. Three of the five platforms we
 * recruit through have no lawful automated route, and saying so is more useful
 * than hiding them and letting someone assume support is pending.
 */
export interface ImportPlatform {
  key: string
  label: string
  available: boolean
  unavailable_reason: string
  notes: string
  accepts: string[]
}

/** The authoritative outcome. Never inferred from the preview. */
export interface ImportCommitResult {
  batch: ImportBatch
  created: number
  updated: number
  duplicate: number
  review: number
  failed: number
  failures: ImportIssue[]
}

export interface ImportErrorReportRow {
  row_number: number
  /** Email, phone or platform id — enough to find the row in their own file. */
  identifier: string
  status: ImportRowStatus
  errors: ImportIssue[]
  warnings: ImportIssue[]
}

export interface ImportErrorReport {
  batch: UUID
  filename: string
  rows: ImportErrorReportRow[]
}

/** Canonical fields a column may be mapped to. Mirrors CANONICAL_FIELDS. */
export const IMPORT_CANONICAL_FIELDS = [
  { value: 'full_name', label: 'Full name', required: true },
  { value: 'first_name', label: 'First name', required: true },
  { value: 'last_name', label: 'Last name', required: false },
  { value: 'email', label: 'Email', required: false },
  { value: 'phone', label: 'Phone', required: false },
  { value: 'external_id', label: 'Platform candidate ID', required: false },
  { value: 'current_employer', label: 'Current employer', required: false },
  { value: 'total_experience_years', label: 'Experience (years)', required: false },
  { value: 'expected_ctc', label: 'Expected CTC', required: false },
  { value: 'notice_period_days', label: 'Notice period', required: false },
] as const
