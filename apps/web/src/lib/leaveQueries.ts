/**
 * Leave module hooks and types. Same conventions as queries.ts: every
 * endpoint declared once, no component builds a URL.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost, http, type Paginated } from './api'
import type { ISODate, ISODateTime, UUID } from './types'

/* ------------------------------------------------------------------ types */

export interface LeaveType {
  id: UUID
  code: string
  name: string
  description: string
  is_paid: boolean
  order: number
}

export interface LeavePolicy {
  id: UUID
  leave_type: UUID
  leave_type_name: string
  name: string
  department: UUID | null
  department_name: string | null
  employment_type: string
  annual_allocation: string
  /** Monthly accrual; '0.00' = the year is credited up front. */
  accrual_per_month: string
  carry_forward: boolean
  carry_forward_limit: string
  allow_half_day: boolean
  requires_attachment: boolean
  min_notice_days: number
  max_consecutive_days: number
  allow_negative_balance: boolean
  min_service_months: number
  is_encashable: boolean
  approval_authority: 'standard' | 'reporting_manager'
}

export interface Holiday {
  id: UUID
  calendar: UUID
  date: ISODate
  name: string
  is_optional: boolean
}

export interface HolidayCalendar {
  id: UUID
  name: string
  location: UUID | null
  location_name: string | null
  weekly_off: number[]
  holidays: Holiday[]
}

export interface LeaveBalance {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  leave_type: UUID
  leave_type_name: string
  leave_type_code: string
  is_paid: boolean
  year: number
  allocated: string
  carried_forward: string
  used: string
  pending: string
  available: string
}

export type LeaveStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'

export interface LeaveRequest {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  department_name: string
  designation_title: string
  leave_type: UUID
  leave_type_name: string
  is_paid: boolean
  start_date: ISODate
  end_date: ISODate
  half_day: '' | 'first_half' | 'second_half'
  days: string
  reason: string
  has_attachment: boolean
  status: LeaveStatus
  approval_band: 'hr' | 'admin'
  /** Where the request stands in the chain: manager first, then hr. */
  approval_stage: 'manager' | 'hr'
  /** Label of who holds it right now — empty once decided. */
  pending_with: string
  can_decide: boolean
  manager_decided_by_email: string
  manager_decided_at: ISODateTime | null
  manager_note: string
  is_emergency: boolean
  /** True when the applicant was on probation: unpaid, no balance impact. */
  probation_unpaid: boolean
  /** What to call the row — "Unpaid Leave – Probation Period" on probation. */
  display_type: string
  decided_by_email: string
  decided_at: ISODateTime | null
  decision_reason: string
  created_at: ISODateTime
}

export interface LeaveSettings {
  leave_year_start_month: number
  long_leave_threshold_days: number
  long_leave_notice_days: number
  single_day_notice_days: number
  general_notice_days: number
  emergency_window_hours: number
  probation_leave_unpaid: boolean
  short_leave_hours_per_day: string
  absence_flag_days: number
  holiday_work_double_pay: boolean
}

export interface ShortLeave {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  date: ISODate
  out_time: string | null
  hours: string
  reason: string
  created_at: ISODateTime
}

export interface HolidayWorkRow {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  date: ISODate
  holiday_name: string
  approved_by_email: string
  note: string
  created_at: ISODateTime
}

export interface LeavePatternRow {
  employee_id: UUID
  employee_name: string
  employee_code: string
  requests: number
  monday_friday: number
  last_minute: number
  emergency: number
  short_leave_hours: string
}

export interface LeaveCalendarRow {
  id: UUID
  employee_name: string
  department_name: string
  leave_type_name: string
  start_date: ISODate
  end_date: ISODate
  half_day: string
  days: string
  status: LeaveStatus
}

/* ------------------------------------------------------------------ hooks */

const invalidateLeave = (queryClient: ReturnType<typeof useQueryClient>) => {
  void queryClient.invalidateQueries({ queryKey: ['leave'] })
}

export function useLeaveTypes() {
  return useQuery({
    queryKey: ['leave', 'types'],
    // The endpoint is paginated like every other list route — unwrap it so
    // consumers get the array they expect.
    queryFn: async () => (await apiGet<Paginated<LeaveType>>('/leave-types/')).data,
    staleTime: 5 * 60 * 1000,
  })
}

export function useLeavePolicies() {
  return useQuery({
    queryKey: ['leave', 'policies'],
    queryFn: () => apiGet<Paginated<LeavePolicy>>('/leave-policies/'),
  })
}

export function useHolidayCalendars() {
  return useQuery({
    queryKey: ['leave', 'calendars'],
    queryFn: () => apiGet<Paginated<HolidayCalendar>>('/holiday-calendars/'),
  })
}

export function useLeaveBalances(params: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: ['leave', 'balances', params],
    queryFn: () =>
      apiGet<Paginated<LeaveBalance>>('/leave-balances/', {
        params: Object.fromEntries(
          Object.entries(params).filter(([, value]) => value),
        ) as Record<string, string>,
      }),
  })
}

export function useLeaveRequests(params: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: ['leave', 'requests', params],
    queryFn: () =>
      apiGet<Paginated<LeaveRequest>>('/leave-requests/', {
        params: Object.fromEntries(
          Object.entries(params).filter(([, value]) => value),
        ) as Record<string, string>,
      }),
  })
}

export function useLeaveCalendar(params: { from?: string; to?: string } = {}) {
  return useQuery({
    queryKey: ['leave', 'calendar', params],
    queryFn: () =>
      apiGet<{ data: LeaveCalendarRow[] }>('/leave-requests/calendar/', {
        params: Object.fromEntries(
          Object.entries(params).filter(([, value]) => value),
        ) as Record<string, string>,
      }),
  })
}

export function useApplyLeave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: {
      leave_type: UUID
      start_date: string
      end_date: string
      half_day: string
      reason: string
      attachment?: File | null
      is_emergency?: boolean
    }) => {
      const body = new FormData()
      body.append('leave_type', payload.leave_type)
      body.append('start_date', payload.start_date)
      body.append('end_date', payload.end_date)
      body.append('half_day', payload.half_day)
      body.append('reason', payload.reason)
      if (payload.is_emergency) body.append('is_emergency', 'true')
      if (payload.attachment) body.append('attachment', payload.attachment)
      // The axios instance pins Content-Type to application/json; clearing it
      // here lets the browser set the multipart boundary — without this, DRF
      // receives the file as a string and refuses with "not a file".
      const { data } = await http.post<LeaveRequest>('/leave-requests/', body, {
        headers: { 'Content-Type': undefined as unknown as string },
      })
      return data
    },
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useLeaveDecision(id: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      action,
      reason,
      note,
    }: {
      action: 'approve' | 'reject' | 'cancel'
      reason?: string
      note?: string
    }) =>
      apiPost<LeaveRequest>(
        `/leave-requests/${id}/${action}/`,
        reason ? { reason } : note ? { note } : {},
      ),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useSaveLeavePolicy(id?: UUID) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: Partial<LeavePolicy>) =>
      id
        ? apiPatch<LeavePolicy>(`/leave-policies/${id}/`, payload)
        : apiPost<LeavePolicy>('/leave-policies/', payload),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useSaveHoliday() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { calendar: UUID; date: string; name: string }) =>
      apiPost<Holiday>('/holidays/', payload),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useDeleteHoliday() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUID) => http.delete(`/holidays/${id}/`).then(() => undefined),
    onSuccess: () => invalidateLeave(queryClient),
  })
}


/* --------------------------------------------------- policy configuration */

export function useLeaveSettings() {
  return useQuery({
    queryKey: ['leave', 'settings'],
    queryFn: () => apiGet<LeaveSettings>('/leave-settings/'),
    staleTime: 5 * 60 * 1000,
  })
}

export function useUpdateLeaveSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: Partial<LeaveSettings>) =>
      apiPatch<LeaveSettings>('/leave-settings/', payload),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useShortLeaves(params: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: ['leave', 'short-leaves', params],
    queryFn: () =>
      apiGet<Paginated<ShortLeave>>('/short-leaves/', {
        params: Object.fromEntries(
          Object.entries(params).filter(([, value]) => value),
        ) as Record<string, string>,
      }),
  })
}

export function useRecordShortLeave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      employee: UUID
      date: string
      hours: string
      out_time?: string
      reason?: string
    }) => apiPost<ShortLeave>('/short-leaves/', payload),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useDeleteShortLeave() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUID) => http.delete(`/short-leaves/${id}/`).then(() => undefined),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useHolidayWorkList(params: Record<string, string | undefined> = {}) {
  return useQuery({
    queryKey: ['leave', 'holiday-work', params],
    queryFn: () =>
      apiGet<Paginated<HolidayWorkRow>>('/holiday-work/', {
        params: Object.fromEntries(
          Object.entries(params).filter(([, value]) => value),
        ) as Record<string, string>,
      }),
  })
}

export function useRecordHolidayWork() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { employee: UUID; date: string; note?: string }) =>
      apiPost<HolidayWorkRow>('/holiday-work/', payload),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useDeleteHolidayWork() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: UUID) => http.delete(`/holiday-work/${id}/`).then(() => undefined),
    onSuccess: () => invalidateLeave(queryClient),
  })
}

export function useLeavePatterns(year: number) {
  return useQuery({
    queryKey: ['leave', 'patterns', year],
    queryFn: () =>
      apiGet<{ data: LeavePatternRow[] }>('/leave-requests/patterns/', {
        params: { year: String(year) },
      }),
  })
}
