/**
 * Attendance data access. The server scopes every list — an employee's
 * queries return their own days, a manager's their team's, HR everyone's.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost, downloadFile, toRelative, type Paginated } from './api'
import type { ListParams } from './queries'
import type { UUID } from './types'

function clean(params: ListParams): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Record<string, string>
}

export interface AttendanceRecordRow {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  date: string
  first_in: string | null
  last_out: string | null
  worked_minutes: number
  status: 'present' | 'half_day' | 'absent' | 'on_leave' | 'holiday' | 'weekly_off'
  is_late: boolean
  late_minutes: number
  /** Only counted lates feed the monthly allowance; a compensated late
   *  (is_late && !late_counted) never penalizes. */
  late_counted: boolean
  source: 'device' | 'manual' | 'regularized'
  notes: string
  reviewed_by: UUID | null
  reviewed_at: string | null
}

export interface MonthCalendarDay {
  date: string
  weekday: string
  kind:
    | 'present' | 'absent' | 'half_day' | 'leave'
    | 'holiday' | 'week_off' | 'no_record' | 'future' | 'not_employed'
  is_late: boolean
  label: string
  first_in: string | null
  last_out: string | null
  worked_minutes: number | null
}

export interface MonthCalendar {
  employee: { id: UUID; employee_code: string; full_name: string; department: string | null }
  year: number
  month: number
  summary: {
    working_days: number
    present_days: number
    absent_days: number
    half_days: number
    late_days: number
    leave_days: string
    week_offs: number
    holidays: number
    no_record_days: number
  }
  days: MonthCalendarDay[]
}

/** One employee's month as a calendar — HR's drill-down from the day view. */
export function useMonthCalendar(employeeId: UUID | undefined, year: number, month: number) {
  return useQuery({
    queryKey: ['attendance-month', employeeId, year, month],
    queryFn: () =>
      apiGet<MonthCalendar>('/attendance-records/month/', {
        params: { employee: employeeId as string, year: String(year), month: String(month) },
      }),
    enabled: Boolean(employeeId),
    placeholderData: (previous) => previous,
  })
}

export interface RegularizationRow {
  id: UUID
  employee: UUID
  employee_name: string
  employee_code: string
  date: string
  proposed_in: string | null
  proposed_out: string | null
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  decided_by: UUID | null
  decided_at: string | null
  decision_note: string
  created_at: string
}

export const attendanceKeys = {
  records: (params: ListParams) => ['attendance-records', params] as const,
  regularizations: (params: ListParams) => ['regularizations', params] as const,
}

export function useAttendanceRecords(params: ListParams = {}) {
  return useQuery({
    queryKey: attendanceKeys.records(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<AttendanceRecordRow>>(toRelative(params.cursor))
        : apiGet<Paginated<AttendanceRecordRow>>('/attendance-records/', {
            params: clean(params),
          }),
    placeholderData: (previous) => previous,
  })
}

export interface PunchRow {
  punched_at: string
  time: string
  device: string
}

/** The raw punches behind one computed day. */
export function useDayPunches(recordId: UUID | null) {
  return useQuery({
    queryKey: ['attendance-records', 'punches', recordId],
    queryFn: () => apiGet<PunchRow[]>(`/attendance-records/${recordId}/punches/`),
    enabled: recordId !== null,
  })
}

/** Stream the scoped day records as a spreadsheet through the auth client. */
export function exportAttendance(input: {
  employee?: string
  date__gte: string
  date__lte: string
  fmt: 'xlsx' | 'csv'
}): Promise<void> {
  const params = new URLSearchParams({
    date__gte: input.date__gte,
    date__lte: input.date__lte,
    fmt: input.fmt,
  })
  if (input.employee) params.set('employee', input.employee)
  return downloadFile(
    `/attendance-records/export/?${params.toString()}`,
    `attendance.${input.fmt}`,
  )
}

export function useAcknowledgeRecord() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: UUID) =>
      apiPost<AttendanceRecordRow>(`/attendance-records/${id}/acknowledge/`, {}),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['attendance-records'] }),
  })
}

export function useCorrectRecord() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: UUID; status: string; notes: string }) =>
      apiPatch<AttendanceRecordRow>(`/attendance-records/${input.id}/`, {
        status: input.status,
        notes: input.notes,
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['attendance-records'] }),
  })
}

export function useRegularizations(params: ListParams = {}) {
  return useQuery({
    queryKey: attendanceKeys.regularizations(params),
    queryFn: () =>
      apiGet<Paginated<RegularizationRow>>('/regularizations/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useRegularizationActions() {
  const client = useQueryClient()
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['regularizations'] })
    void client.invalidateQueries({ queryKey: ['attendance-records'] })
  }
  return {
    create: useMutation({
      mutationFn: (input: {
        date: string
        proposed_in?: string
        proposed_out?: string
        reason: string
      }) => apiPost<RegularizationRow>('/regularizations/', input),
      onSuccess: invalidate,
    }),
    approve: useMutation({
      mutationFn: (input: { id: UUID; note?: string }) =>
        apiPost<RegularizationRow>(`/regularizations/${input.id}/approve/`, {
          note: input.note ?? '',
        }),
      onSuccess: invalidate,
    }),
    reject: useMutation({
      mutationFn: (input: { id: UUID; note?: string }) =>
        apiPost<RegularizationRow>(`/regularizations/${input.id}/reject/`, {
          note: input.note ?? '',
        }),
      onSuccess: invalidate,
    }),
  }
}
