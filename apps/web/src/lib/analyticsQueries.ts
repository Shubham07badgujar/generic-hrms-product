/**
 * BI, notification and audit hooks.
 *
 * Nothing here computes a metric, decides who may see one, or classifies an
 * audit event as sensitive. All three are server answers — the catalogue is
 * already filtered to what the caller may see, `scope_label` describes the
 * breadth the server actually applied, and `is_sensitive` is the server's
 * classification. The UI renders those rather than deriving its own, so a
 * screen can never imply a number is organisation-wide when it is not.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, type Paginated } from './api'
import type { ListParams } from './queries'
import type {
  AuditEntry,
  AuditOptions,
  MetricCatalog,
  MetricResult,
  NotificationItem,
  NotificationPreferenceRow,
  UUID,
} from './types'

function clean(params: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== '' && v !== null),
  ) as Record<string, string>
}

export const analyticsKeys = {
  catalog: ['bi', 'catalog'] as const,
  metric: (key: string, params?: unknown) => ['bi', 'metric', key, params ?? {}] as const,
  audit: (params?: unknown) => ['audit', params ?? {}] as const,
  auditOptions: ['audit', 'options'] as const,
  notifications: (params?: unknown) => ['notifications', params ?? {}] as const,
  unread: ['notifications', 'unread-count'] as const,
  preferences: ['notifications', 'preferences'] as const,
}

/* ------------------------------------------------------------------- BI */

/** What this caller may see. Already filtered server-side. */
export function useMetricCatalog(enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.catalog,
    queryFn: () => apiGet<MetricCatalog>('/bi/metrics/'),
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}

export interface MetricParams {
  start?: string
  end?: string
  group_by?: string
  [key: string]: string | undefined
}

export function useMetric(key: string | undefined, params: MetricParams = {}, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.metric(key ?? 'none', params),
    queryFn: () => apiGet<MetricResult>(`/bi/${key}/`, { params: clean(params) }),
    enabled: Boolean(key) && enabled,
    placeholderData: (previous) => previous,
  })
}

/**
 * Several metrics for one dashboard.
 *
 * Each is its own query so one failing tile does not blank the page — a metric
 * the caller cannot see simply never resolves, and its card renders empty
 * rather than taking the others down with it.
 */
export function useMetrics(keys: string[], params: MetricParams = {}) {
  const results = keys.map((key) => useMetric(key, params))
  return {
    results,
    byKey: Object.fromEntries(keys.map((key, index) => [key, results[index]])),
    isLoading: results.some((r) => r.isLoading),
  }
}

/* --------------------------------------------------------- notifications */

export function useNotificationList(params: ListParams = {}) {
  return useQuery({
    queryKey: analyticsKeys.notifications(params),
    queryFn: () =>
      apiGet<Paginated<NotificationItem>>('/notifications/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useNotificationPreferences(enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.preferences,
    queryFn: () =>
      apiGet<{ preferences: NotificationPreferenceRow[] }>('/notifications/preferences/'),
    enabled,
  })
}

export function useSetNotificationPreference() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { kind: string; in_app: boolean; email: boolean }) =>
      apiPost<NotificationPreferenceRow>('/notifications/preferences/', body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: analyticsKeys.preferences })
    },
  })
}

/* ---------------------------------------------------------------- audit */

export interface AuditParams extends ListParams {
  action?: string
  resource?: string
  entity_type?: string
  subject_employee?: UUID
  since?: string
  until?: string
  sensitive_only?: string
}

export function useAuditLog(params: AuditParams = {}) {
  return useQuery({
    queryKey: analyticsKeys.audit(params),
    queryFn: () => apiGet<Paginated<AuditEntry>>('/audit/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useAuditOptions(enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.auditOptions,
    queryFn: () => apiGet<AuditOptions>('/audit/options/'),
    enabled,
    staleTime: 5 * 60 * 1000,
  })
}
