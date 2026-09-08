/**
 * Offboarding hooks.
 *
 * `blockers` and `can_complete` arrive from the server on the detail payload —
 * this module never computes them. That is the whole arrangement: one function
 * on the backend decides whether an exit may complete, guards both approval and
 * completion, and hands the same answer to the UI.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, type Paginated, toRelative } from './api'
import type { ListParams } from './queries'
import type {
  ExitClearanceItem,
  ExitInterview,
  ExitWorkflowDetail,
  ExitWorkflowSummary,
  FinalSettlement,
  ResignationRequest,
  UUID,
} from './types'

function clean(params: ListParams): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Record<string, string>
}

export const exitKeys = {
  resignations: (params?: unknown) => ['resignations', params ?? {}] as const,
  pendingResignations: ['resignations', 'pending'] as const,
  exits: (params?: unknown) => ['exits', params ?? {}] as const,
  exit: (id: UUID) => ['exits', 'detail', id] as const,
  clearance: (params?: unknown) => ['exit-clearance-items', params ?? {}] as const,
  myClearance: ['exit-clearance-items', 'mine'] as const,
}

/**
 * One exit action can change several parts of the page at once — completing a
 * clearance item changes the item, the progress bar AND the blocker list — so
 * everything the detail view shows is refreshed together.
 */
function useExitInvalidator(exitId?: UUID) {
  const queryClient = useQueryClient()
  return () => {
    if (exitId) void queryClient.invalidateQueries({ queryKey: exitKeys.exit(exitId) })
    for (const key of [
      'exits', 'resignations', 'exit-clearance-items',
      'employees', 'asset-allocations', 'company-accounts',
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }
}

/* --------------------------------------------------------- resignations */

export function useResignations(params: ListParams = {}) {
  return useQuery({
    queryKey: exitKeys.resignations(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<ResignationRequest>>(toRelative(params.cursor))
        : apiGet<Paginated<ResignationRequest>>('/resignations/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function usePendingResignations(enabled = true) {
  return useQuery({
    queryKey: exitKeys.pendingResignations,
    queryFn: () => apiGet<Paginated<ResignationRequest>>('/resignations/pending/'),
    enabled,
  })
}

export function useSubmitResignation() {
  const invalidate = useExitInvalidator()
  return useMutation({
    mutationFn: (body: {
      employee?: UUID
      requested_last_working_date: string
      reason: string
      comments?: string
    }) => apiPost<ResignationRequest>('/resignations/', body),
    onSuccess: invalidate,
  })
}

export function useResignationReview(id: UUID) {
  const invalidate = useExitInvalidator()
  return {
    approve: useMutation({
      mutationFn: (body: {
        approved_last_working_date?: string
        notes?: string
        notice_days?: number
      }) => apiPost<ExitWorkflowDetail>(`/resignations/${id}/approve/`, body),
      onSuccess: invalidate,
    }),
    reject: useMutation({
      mutationFn: (body: { reason: string }) =>
        apiPost<ResignationRequest>(`/resignations/${id}/reject/`, body),
      onSuccess: invalidate,
    }),
    withdraw: useMutation({
      mutationFn: () => apiPost<ResignationRequest>(`/resignations/${id}/withdraw/`),
      onSuccess: invalidate,
    }),
  }
}

/* ---------------------------------------------------------------- exits */

export function useExits(params: ListParams = {}) {
  return useQuery({
    queryKey: exitKeys.exits(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<ExitWorkflowSummary>>(toRelative(params.cursor))
        : apiGet<Paginated<ExitWorkflowSummary>>('/exits/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useExit(id: UUID | undefined) {
  return useQuery({
    queryKey: exitKeys.exit(id ?? 'none'),
    queryFn: () => apiGet<ExitWorkflowDetail>(`/exits/${id}/`),
    enabled: Boolean(id),
  })
}

export function useStartExit() {
  const invalidate = useExitInvalidator()
  return useMutation({
    mutationFn: (body: {
      employee: UUID
      exit_type: string
      last_working_date: string
      reason?: string
      notice_days?: number
    }) => apiPost<ExitWorkflowDetail>('/exits/', body),
    onSuccess: invalidate,
  })
}

export function useExitActions(id: UUID) {
  const invalidate = useExitInvalidator(id)
  const mutate = <T,>(path: string) =>
    useMutation({
      mutationFn: (body: T) => apiPost<ExitWorkflowDetail>(`/exits/${id}/${path}/`, body),
      onSuccess: invalidate,
    })

  return {
    // Notice exceptions. Both take OFFBOARDING/APPROVE and a substantial reason.
    waiveNotice: mutate<{ reason: string; new_last_working_date?: string }>('waive-notice'),
    earlyRelease: mutate<{ reason: string; new_last_working_date: string }>('early-release'),
    updateNotice: mutate<{ last_working_date?: string; notice_days?: number }>('update-notice'),
    // Refused by the server while any gate is open.
    approve: mutate<{ notes?: string }>('approve'),
    complete: mutate<{ actual_last_working_date?: string }>('complete'),
    cancel: mutate<{ reason: string }>('cancel'),
    interview: useMutation({
      mutationFn: (body: Partial<ExitInterview>) =>
        apiPost<ExitInterview>(`/exits/${id}/interview/`, body),
      onSuccess: invalidate,
    }),
    settlement: useMutation({
      mutationFn: (body: Partial<FinalSettlement>) =>
        apiPost<FinalSettlement>(`/exits/${id}/settlement/`, body),
      onSuccess: invalidate,
    }),
    clearSettlement: useMutation({
      mutationFn: (body: { notes?: string }) =>
        apiPost<FinalSettlement>(`/exits/${id}/clear-settlement/`, body),
      onSuccess: invalidate,
    }),
  }
}

/* ------------------------------------------------------------ clearance */

export function useMyClearanceItems(enabled = true) {
  return useQuery({
    queryKey: exitKeys.myClearance,
    queryFn: () => apiGet<ExitClearanceItem[]>('/exit-clearance-items/mine/'),
    enabled,
  })
}

export function useClearanceActions(exitId?: UUID) {
  const invalidate = useExitInvalidator(exitId)
  return {
    complete: useMutation({
      mutationFn: (input: { id: UUID; notes?: string }) =>
        apiPost<ExitClearanceItem>(`/exit-clearance-items/${input.id}/complete/`, {
          notes: input.notes ?? '',
        }),
      onSuccess: invalidate,
    }),
    waive: useMutation({
      mutationFn: (input: { id: UUID; reason: string }) =>
        apiPost<ExitClearanceItem>(`/exit-clearance-items/${input.id}/waive/`, {
          reason: input.reason,
        }),
      onSuccess: invalidate,
    }),
  }
}
