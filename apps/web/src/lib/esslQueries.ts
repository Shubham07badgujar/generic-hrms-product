/**
 * The eSSL integration's admin surface. Every route here is gated on the
 * attendance_device resource server-side; credentials never appear in any
 * payload — the status endpoint returns the host and booleans only.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from './api'
import type { UUID } from './types'

export interface EsslDevice {
  id: UUID
  name: string
  serial_number: string
  location: UUID | null
  location_name: string | null
  is_enabled: boolean
  last_synced_at: string | null
  last_sync_status: 'never' | 'ok' | 'failed'
  last_sync_error: string
}

export interface EsslMapping {
  id: UUID
  essl_user_id: string
  employee: UUID
  employee_name: string
  employee_code: string
  /** The site this ID belongs to. Null on mappings made before multi-site support. */
  location: UUID | null
  location_name: string | null
}

export interface EsslSyncRun {
  id: UUID
  kind: 'scheduled' | 'manual' | 'range'
  range_from: string | null
  range_to: string | null
  started_at: string
  finished_at: string | null
  status: 'running' | 'completed' | 'partial' | 'failed'
  devices_total: number
  devices_failed: number
  punches_fetched: number
  punches_created: number
  punches_duplicate: number
  punches_unmapped: number
  errors: Array<{ device: string; error: string }>
}

export interface EsslStatus {
  enabled: boolean
  base_host: string
  affects_payroll: boolean
  devices: EsslDevice[]
  last_run: EsslSyncRun | null
}

export interface UnmappedRow {
  essl_user_id: string
  punches: number
  first_seen: string
  last_seen: string
}

export interface ShiftRuleRow {
  id: UUID
  location: UUID | null
  location_name: string | null
  start_time: string
  end_time: string
  grace_minutes: number
  allowed_late_per_month: number
  half_day_below_hours: string
  /** A late arrival reaching this many worked hours is compensated. */
  full_day_hours: string
}

export const esslKeys = {
  status: ['essl', 'status'] as const,
  mappings: ['essl', 'mappings'] as const,
  unmapped: ['essl', 'unmapped'] as const,
  runs: ['essl', 'runs'] as const,
  shiftRules: ['essl', 'shift-rules'] as const,
}

export function useEsslStatus() {
  return useQuery({
    queryKey: esslKeys.status,
    queryFn: () => apiGet<EsslStatus>('/essl/devices/status/'),
  })
}

export function useEsslMappings() {
  return useQuery({
    queryKey: esslKeys.mappings,
    queryFn: () => apiGet<EsslMapping[]>('/essl/mappings/'),
  })
}

export function useUnmapped() {
  return useQuery({
    queryKey: esslKeys.unmapped,
    queryFn: () => apiGet<UnmappedRow[]>('/essl/devices/unmapped/'),
  })
}

export function useSyncRuns() {
  return useQuery({
    queryKey: esslKeys.runs,
    queryFn: () => apiGet<EsslSyncRun[]>('/essl/devices/runs/'),
  })
}

export function useShiftRules() {
  return useQuery({
    queryKey: esslKeys.shiftRules,
    queryFn: () => apiGet<ShiftRuleRow[]>('/essl/shift-rules/'),
  })
}

export function useUpdateShiftRule() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: UUID } & Partial<Omit<ShiftRuleRow, 'id' | 'location' | 'location_name'>>) => {
      const { id, ...payload } = input
      return apiPatch<ShiftRuleRow>(`/essl/shift-rules/${id}/`, payload)
    },
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: esslKeys.shiftRules }),
  })
}

export function useEsslActions() {
  const client = useQueryClient()
  const invalidate = () => void client.invalidateQueries({ queryKey: ['essl'] })
  return {
    createDevice: useMutation({
      mutationFn: (input: { name: string; serial_number: string; location?: UUID | null }) =>
        apiPost<EsslDevice>('/essl/devices/', input),
      onSuccess: invalidate,
    }),
    updateDevice: useMutation({
      mutationFn: ({ id, ...body }: { id: UUID; name?: string; is_enabled?: boolean; location?: UUID | null }) =>
        apiPatch<EsslDevice>(`/essl/devices/${id}/`, body),
      onSuccess: invalidate,
    }),
    removeDevice: useMutation({
      mutationFn: (id: UUID) => apiDelete(`/essl/devices/${id}/`),
      onSuccess: invalidate,
    }),
    createMapping: useMutation({
      mutationFn: (input: { essl_user_id: string; employee: UUID; location?: UUID | null }) =>
        apiPost<EsslMapping>('/essl/mappings/', input),
      onSuccess: () => {
        invalidate()
        void client.invalidateQueries({ queryKey: ['attendance-records'] })
      },
    }),
    removeMapping: useMutation({
      mutationFn: (id: UUID) => apiDelete(`/essl/mappings/${id}/`),
      onSuccess: () => {
        invalidate()
        void client.invalidateQueries({ queryKey: ['attendance-records'] })
      },
    }),
    updateMapping: useMutation({
      mutationFn: ({ id, ...body }: { id: UUID; location?: UUID | null }) =>
        apiPatch<EsslMapping>(`/essl/mappings/${id}/`, body),
      onSuccess: invalidate,
    }),
    syncNow: useMutation({
      mutationFn: () => apiPost<EsslSyncRun>('/essl/devices/sync-now/', {}),
      onSuccess: () => {
        invalidate()
        void client.invalidateQueries({ queryKey: ['attendance-records'] })
      },
    }),
    syncRange: useMutation({
      mutationFn: (input: { date_from: string; date_to: string }) =>
        apiPost<EsslSyncRun>('/essl/devices/sync-range/', input),
      onSuccess: () => {
        invalidate()
        void client.invalidateQueries({ queryKey: ['attendance-records'] })
      },
    }),
  }
}
