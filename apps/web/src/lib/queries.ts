/**
 * Query and mutation hooks.
 *
 * Every endpoint the SPA uses is declared here once, so no component builds a
 * URL. That matters more than usual with this backend: the action routes are
 * split by permission (`/reject/` vs `/override/` vs `/advance/`), and a
 * component assembling paths by hand would eventually post a rejection to the
 * advance route and get a confusing 400.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost, http, type Paginated, toRelative } from './api'
import type {
  Application,
  ApplicationHistory,
  Candidate,
  CandidateNotification,
  CandidateHistory,
  ConflictCheck,
  ConversionResult,
  DecisionOverride,
  DecisionResult,
  Department,
  Designation,
  EmployeeCreatePayload,
  EmployeeCreateResult,
  EmployeeDetail,
  EmployeeLevel,
  EmployeeListItem,
  FeedbackForm,
  HiringWorkflow,
  HiringWorkflowSummary,
  Interview,
  InterviewFeedback,
  JobOpening,
  Location,
  Offer,
  Role,
  UUID,
} from './types'

/* ------------------------------------------------------------------ keys */

export const queryKeys = {
  reference: ['reference'] as const,
  departments: ['reference', 'departments'] as const,
  designations: ['reference', 'designations'] as const,
  locations: ['reference', 'locations'] as const,
  levels: ['reference', 'levels'] as const,
  roles: ['reference', 'roles'] as const,

  workflows: ['workflows'] as const,
  workflow: (id: UUID) => ['workflows', id] as const,
  feedbackForms: ['feedback-forms'] as const,

  jobs: (params?: unknown) => ['jobs', params ?? {}] as const,
  job: (id: UUID) => ['jobs', 'detail', id] as const,

  candidates: (params?: unknown) => ['candidates', params ?? {}] as const,
  candidate: (id: UUID) => ['candidates', 'detail', id] as const,
  candidateHistory: (id: UUID) => ['candidates', 'detail', id, 'history'] as const,

  applications: (params?: unknown) => ['applications', params ?? {}] as const,
  application: (id: UUID) => ['applications', 'detail', id] as const,
  applicationHistory: (id: UUID) => ['applications', 'detail', id, 'history'] as const,
  applicationNotifications: (id: UUID) => ['applications', 'detail', id, 'notifications'] as const,
  hrQueue: ['applications', 'pending-hr-decision'] as const,

  interviews: (params?: unknown) => ['interviews', params ?? {}] as const,
  offers: (params?: unknown) => ['offers', params ?? {}] as const,

  employees: (params?: unknown) => ['employees', params ?? {}] as const,
  employee: (id: UUID) => ['employees', 'detail', id] as const,
  myEmployee: ['employees', 'me'] as const,
}

/** Reference data barely changes; caching it for the session avoids refetching
 *  a department list on every form open. */
const REFERENCE_OPTIONS = { staleTime: 15 * 60 * 1000, gcTime: 30 * 60 * 1000 }

type QueryOpts<T> = Omit<UseQueryOptions<T, unknown, T, readonly unknown[]>, 'queryKey' | 'queryFn'>

/* ------------------------------------------------------------- reference */

export interface OrgSettingsRow {
  id: string
  name: string
  legal_name: string
  currency: string
  timezone: string
  financial_year_start_month: number
  employee_code_prefix: string
  signatory_name: string
  signatory_designation: string
  has_logo: boolean
  has_signature: boolean
}

export function useOrgSettings(enabled = true) {
  return useQuery({
    queryKey: ['org-settings'],
    queryFn: () => apiGet<OrgSettingsRow>('/org/settings/'),
    enabled,
  })
}

export function useUpdateOrgSettings() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      name?: string
      legal_name?: string
      signatory_name?: string
      signatory_designation?: string
      logo?: File | null
      signature?: File | null
    }) => {
      const body = new FormData()
      for (const key of ['name', 'legal_name', 'signatory_name', 'signatory_designation'] as const) {
        const value = input[key]
        if (value !== undefined) body.append(key, value)
      }
      if (input.logo) body.append('logo', input.logo)
      if (input.signature) body.append('signature', input.signature)
      // The axios instance pins Content-Type to application/json; clearing it
      // lets the browser set the multipart boundary (house pattern).
      return http
        .patch<OrgSettingsRow>('/org/settings/', body, {
          headers: { 'Content-Type': undefined as unknown as string },
        })
        .then((response) => response.data)
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['org-settings'] }),
  })
}

export function useDepartments(options?: QueryOpts<Department[]>) {
  return useQuery({
    queryKey: queryKeys.departments,
    queryFn: () => apiGet<Department[]>('/departments/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

export function useDesignations(options?: QueryOpts<Designation[]>) {
  return useQuery({
    queryKey: queryKeys.designations,
    queryFn: () => apiGet<Designation[]>('/designations/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

export function useLocations(options?: QueryOpts<Location[]>) {
  return useQuery({
    queryKey: queryKeys.locations,
    queryFn: () => apiGet<Location[]>('/locations/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

export function useEmployeeLevels(options?: QueryOpts<EmployeeLevel[]>) {
  return useQuery({
    queryKey: queryKeys.levels,
    queryFn: () => apiGet<EmployeeLevel[]>('/levels/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

export function useRoles(options?: QueryOpts<Role[]>) {
  return useQuery({
    queryKey: queryKeys.roles,
    queryFn: () => apiGet<Role[]>('/roles/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

/* -------------------------------------------------------------- workflow */

export function useWorkflows(options?: QueryOpts<Paginated<HiringWorkflowSummary>>) {
  return useQuery({
    queryKey: queryKeys.workflows,
    queryFn: () => apiGet<Paginated<HiringWorkflowSummary>>('/workflows/'),
    ...REFERENCE_OPTIONS,
    ...options,
  })
}

export function useWorkflow(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.workflow(id ?? 'none'),
    queryFn: () => apiGet<HiringWorkflow>(`/workflows/${id}/`),
    enabled: Boolean(id),
    ...REFERENCE_OPTIONS,
  })
}

export function useFeedbackForms() {
  return useQuery({
    queryKey: queryKeys.feedbackForms,
    queryFn: () => apiGet<Paginated<FeedbackForm>>('/feedback-forms/'),
    ...REFERENCE_OPTIONS,
  })
}

/* ------------------------------------------------------------------ jobs */

export interface ListParams {
  search?: string
  ordering?: string
  cursor?: string
  [key: string]: string | undefined
}

function clean(params: ListParams): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Record<string, string>
}

export function useJobs(params: ListParams = {}) {
  return useQuery({
    queryKey: queryKeys.jobs(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<JobOpening>>(toRelative(params.cursor))
        : apiGet<Paginated<JobOpening>>('/jobs/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useJob(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.job(id ?? 'none'),
    queryFn: () => apiGet<JobOpening>(`/jobs/${id}/`),
    enabled: Boolean(id),
  })
}

export function useSaveJob(id?: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: Partial<JobOpening>) =>
      id ? apiPatch<JobOpening>(`/jobs/${id}/`, payload) : apiPost<JobOpening>('/jobs/', payload),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.setQueryData(queryKeys.job(job.id), job)
    },
  })
}

export function useJobLifecycle(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (action: 'publish' | 'close') => apiPost<JobOpening>(`/jobs/${id}/${action}/`),
    onSuccess: (job) => {
      queryClient.setQueryData(queryKeys.job(id), job)
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/* ------------------------------------------------------------ candidates */

export function useCandidates(params: ListParams = {}) {
  return useQuery({
    queryKey: queryKeys.candidates(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<Candidate>>(toRelative(params.cursor))
        : apiGet<Paginated<Candidate>>('/candidates/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useCandidate(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.candidate(id ?? 'none'),
    queryFn: () => apiGet<Candidate>(`/candidates/${id}/`),
    enabled: Boolean(id),
  })
}

export function useCandidateHistory(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.candidateHistory(id ?? 'none'),
    queryFn: () => apiGet<CandidateHistory>(`/candidates/${id}/history/`),
    enabled: Boolean(id),
  })
}

export function useCreateCandidate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: Partial<Candidate>) => apiPost<Candidate>('/candidates/', payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['candidates'] }),
  })
}

/* ---------------------------------------------------------- applications */

export function useApplications(params: ListParams = {}) {
  return useQuery({
    queryKey: queryKeys.applications(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<Application>>(toRelative(params.cursor))
        : apiGet<Paginated<Application>>('/applications/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useApplication(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.application(id ?? 'none'),
    queryFn: () => apiGet<Application>(`/applications/${id}/`),
    enabled: Boolean(id),
  })
}

export function useApplicationHistory(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.applicationHistory(id ?? 'none'),
    queryFn: () => apiGet<ApplicationHistory>(`/applications/${id}/history/`),
    enabled: Boolean(id),
  })
}

export function useApplicationNotifications(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.applicationNotifications(id ?? 'none'),
    queryFn: () => apiGet<CandidateNotification[]>(`/applications/${id}/notifications/`),
    enabled: Boolean(id),
  })
}

export function useRetryNotification(applicationId: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (notification: UUID) =>
      apiPost<CandidateNotification>(`/applications/${applicationId}/retry-notification/`, {
        notification,
      }),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: queryKeys.applicationNotifications(applicationId),
      }),
  })
}

export function useHrDecisionQueue(enabled = true) {
  return useQuery({
    queryKey: queryKeys.hrQueue,
    queryFn: () => apiGet<Paginated<Application>>('/applications/pending-hr-decision/'),
    enabled,
  })
}

export function useCreateApplication() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { candidate: UUID; job_opening: UUID }) =>
      apiPost<Application>('/applications/', payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['applications'] }),
  })
}

/**
 * Invalidates everything a decision can change.
 *
 * A stage move rewrites the application, its history, the HR queue and the
 * pipeline counts. Invalidating only the detail leaves the queue showing a
 * candidate who has already been decided — which reads as a broken system.
 */
function useApplicationInvalidator(id: UUID) {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.application(id) })
    void queryClient.invalidateQueries({ queryKey: queryKeys.applicationHistory(id) })
    void queryClient.invalidateQueries({ queryKey: queryKeys.hrQueue })
    void queryClient.invalidateQueries({ queryKey: ['applications'] })
    void queryClient.invalidateQueries({ queryKey: ['interviews'] })
    void queryClient.invalidateQueries({ queryKey: ['offers'] })
  }
}

/** `POST /applications/{id}/advance/` — pass, verify, request_info. */
export function useAdvanceApplication(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: (body: { decision: string; rationale?: string; interview?: UUID }) =>
      apiPost<DecisionResult>(`/applications/${id}/advance/`, body),
    onSuccess: invalidate,
  })
}

/** `POST /applications/{id}/recommend/` — advisory only; never terminal. */
export function useRecommendApplication(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: (body: { decision: string; rationale: string }) =>
      apiPost<DecisionResult>(`/applications/${id}/recommend/`, body),
    onSuccess: invalidate,
  })
}

/** `POST /applications/{id}/select/` — HR Head. */
export function useSelectCandidate(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: () => apiPost<DecisionResult>(`/applications/${id}/select/`),
    onSuccess: invalidate,
  })
}

/** `POST /applications/{id}/reject/` — HR HEAD ONLY, reason mandatory. */
export function useRejectCandidate(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: (reason: string) =>
      apiPost<DecisionResult>(`/applications/${id}/reject/`, { reason }),
    onSuccess: invalidate,
  })
}

/**
 * `POST /applications/{id}/slot-invite/` — HR configures the current round's
 * available times; only then does the candidate get the booking link.
 */
export function useConfigureSlotInvite(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: (body: { options: { start: string; end: string }[] }) =>
      apiPost<{ id: UUID; status: string; selection_url: string }>(
        `/applications/${id}/slot-invite/`,
        body,
      ),
    onSuccess: invalidate,
  })
}

/** `POST /applications/{id}/override/` — ADMIN ONLY, separate audited record. */
export function useOverrideDecision(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  return useMutation({
    mutationFn: (body: { new_status: string; reason: string }) =>
      apiPost<DecisionOverride>(`/applications/${id}/override/`, body),
    onSuccess: invalidate,
  })
}

/** `POST /applications/{id}/convert/` — candidate → employee, atomically. */
export function useConvertToEmployee(id: UUID) {
  const invalidate = useApplicationInvalidator(id)
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      first_name?: string
      last_name?: string
      email?: string
      personal_email?: string
      phone?: string
      department?: UUID
      designation?: UUID
      location?: UUID
      reporting_manager?: UUID
      date_of_joining?: string
    }) => apiPost<ConversionResult>(`/applications/${id}/convert/`, body),
    onSuccess: () => {
      invalidate()
      void queryClient.invalidateQueries({ queryKey: ['employees'] })
    },
  })
}

/* ------------------------------------------------------------ interviews */

export function useInterviews(params: ListParams = {}) {
  return useQuery({
    queryKey: queryKeys.interviews(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<Interview>>(toRelative(params.cursor))
        : apiGet<Paginated<Interview>>('/interviews/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

/**
 * The live conflict check — layer 3 of the three-layer double-booking guard.
 *
 * ADVISORY ONLY. The service re-checks inside a locked transaction and a
 * PostgreSQL exclusion constraint refuses the write regardless. A slot that
 * looks free here can still be taken before submit, and the 400 that follows
 * is correct behaviour, not a bug in this query.
 */
export function useConflictCheck(params: {
  interviewer?: UUID
  start?: string
  end?: string
  exclude?: UUID
}) {
  const ready = Boolean(params.interviewer && params.start && params.end)
  return useQuery({
    queryKey: ['interviews', 'conflict', params],
    queryFn: () =>
      apiGet<ConflictCheck>('/interviews/check-conflict/', {
        params: clean(params as ListParams),
      }),
    enabled: ready,
    staleTime: 0,
    retry: false,
  })
}

/**
 * Who may take a given interview round.
 *
 * The server decides, from the stage's own role and who is still employed.
 * The employee directory is NOT the right source: it is scoped to what the
 * caller may see, so a round whose interviewer sits in another department
 * silently vanished from the picker.
 */
export interface EligibleInterviewer {
  id: UUID
  full_name: string
  employee_code: string
  department_name: string
  designation_title: string
  status: string
}

export interface EligibleInterviewers {
  stage: UUID
  stage_name: string
  role_code: string | null
  role_name: string
  data: EligibleInterviewer[]
  /** Filled only when nobody is eligible — says which situation it is. */
  problem: string
}

export function useEligibleInterviewers(stage: UUID | undefined) {
  return useQuery({
    queryKey: ['interviews', 'eligible', stage ?? 'none'],
    queryFn: () =>
      apiGet<EligibleInterviewers>('/interviews/eligible-interviewers/', {
        params: { stage: stage as string },
      }),
    enabled: Boolean(stage),
    staleTime: 30_000,
  })
}

export function useScheduleInterview() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      application: UUID
      stage: UUID
      interviewer: UUID
      scheduled_at: string
      duration_minutes?: number
      mode?: string
      location_or_link?: string
    }) => apiPost<Interview>('/interviews/', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
      void queryClient.invalidateQueries({ queryKey: ['applications'] })
    },
  })
}

export function useRescheduleInterview(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { scheduled_at: string; duration_minutes?: number }) =>
      apiPost<Interview>(`/interviews/${id}/reschedule/`, payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['interviews'] }),
  })
}

export function useCancelInterview(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { reason?: string }) =>
      apiPost<Interview>(`/interviews/${id}/cancel/`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
      void queryClient.invalidateQueries({ queryKey: ['applications'] })
    },
  })
}

/** `POST /interviews/{id}/reject-time/` — reject the TIME, rebook the candidate. */
export function useRejectInterviewTime(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { reason: string; options?: { start: string; end: string }[] }) =>
      apiPost<Interview>(`/interviews/${id}/reject-time/`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
      void queryClient.invalidateQueries({ queryKey: ['applications'] })
    },
  })
}

/** `POST /interviews/{id}/retry-calendar/` — retry a failed Google Calendar sync. */
export function useRetryCalendarSync(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<Interview>(`/interviews/${id}/retry-calendar/`, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
    },
  })
}

export function useSubmitFeedback(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      answers: Record<string, string | number | boolean>
      recommendation: string
      strengths?: string
      concerns?: string
      overall_rating?: number
    }) => apiPost<InterviewFeedback>(`/interviews/${id}/feedback/`, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['interviews'] })
      void queryClient.invalidateQueries({ queryKey: ['applications'] })
    },
  })
}

/* ---------------------------------------------------------------- offers */

export function useOffers(params: ListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.offers(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<Offer>>(toRelative(params.cursor))
        : apiGet<Paginated<Offer>>('/offers/', { params: clean(params) }),
    placeholderData: (previous) => previous,
    enabled: options.enabled ?? true,
  })
}

export function useCreateOffer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      application: UUID
      offered_ctc: string
      joining_date: string
      valid_until?: string
      designation?: UUID
      level?: UUID
      reporting_manager?: UUID
    }) => apiPost<Offer>('/offers/', payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['offers'] })
      void queryClient.invalidateQueries({ queryKey: ['applications'] })
    },
  })
}

export function useOfferAction(id: UUID) {
  const queryClient = useQueryClient()
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['offers'] })
    void queryClient.invalidateQueries({ queryKey: ['applications'] })
  }
  return {
    send: useMutation({
      mutationFn: () => apiPost<Offer>(`/offers/${id}/send/`),
      onSuccess: invalidate,
    }),
    respond: useMutation({
      mutationFn: (body: { accepted: boolean; note?: string }) =>
        apiPost<Offer>(`/offers/${id}/respond/`, body),
      onSuccess: invalidate,
    }),
  }
}

/* ------------------------------------------------------------- employees */

export function useEmployees(params: ListParams = {}) {
  return useQuery({
    queryKey: queryKeys.employees(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<EmployeeListItem>>(toRelative(params.cursor))
        : apiGet<Paginated<EmployeeListItem>>('/employees/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

/**
 * Employees who are still with the organisation — the people a picker may
 * offer as a manager, interviewer, asset holder or leaver.
 *
 * NOT `status=active`. In this system "active" is the status BEFORE
 * confirmation, and in a mature organisation nearly everyone is confirmed; a
 * picker that asked for active was empty for exactly the people who belonged
 * on it (the Reporting Manager and Interviewer dropdowns both shipped that
 * way). "Current" means not exited and not terminated; the server re-checks
 * whatever else a given action needs (role, hierarchy, scope).
 */
export const GONE_STATUSES: ReadonlySet<string> = new Set(['exited', 'terminated'])

export function isCurrentEmployee(employee: { status: string }): boolean {
  return !GONE_STATUSES.has(employee.status)
}

export function useCurrentEmployees(params: ListParams = {}) {
  const query = useEmployees({ page_size: '200', ...params })
  const rows = (query.data?.data ?? []).filter(isCurrentEmployee)
  return { ...query, rows }
}

/**
 * Remove a FORMER employee (exited/terminated) from the system. The server
 * soft-deletes the record, switches the login off and audits it; anyone still
 * on the books is refused with the reason.
 */
export function useRemoveEmployee(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (reason: string) =>
      http.delete(`/employees/${id}/`, { data: { reason } }).then(() => undefined),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['employees'] })
    },
  })
}

export function useEmployee(id: UUID | undefined) {
  return useQuery({
    queryKey: queryKeys.employee(id ?? 'none'),
    queryFn: () => apiGet<EmployeeDetail>(`/employees/${id}/`),
    enabled: Boolean(id),
  })
}

export function useMyEmployeeRecord(enabled = true) {
  return useQuery({
    queryKey: queryKeys.myEmployee,
    queryFn: () => apiGet<EmployeeDetail>('/employees/me/'),
    enabled,
    retry: false,
  })
}

export function useCreateEmployee() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: EmployeeCreatePayload) =>
      apiPost<EmployeeCreateResult>('/employees/', payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['employees'] }),
  })
}
