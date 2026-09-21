/**
 * The console's data layer.
 *
 * Every key starts with `'platform'`, and every URL with `/platform/`. That is
 * not tidiness: `queryClient.clear()` runs on sign-out, but while a session is
 * live the cache is the one place two products could meet. A key prefix that
 * cannot collide with an HR screen's means a console response can never be
 * served to an organization screen asking a similar question, and the reverse.
 *
 * The writes all target the same detail object, so each invalidates the list
 * and the detail together. An operator who changes a plan and then reads the
 * list must not see the old plan sitting there.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, type Paginated } from '@/lib/api'
import type {
  InvitationResend,
  PlatformOrganization,
  PlatformPlan,
  PlatformSummary,
  ProvisionRequest,
  ProvisionedOrganization,
} from './types'

const KEY = 'platform'

export function usePlatformSummary() {
  return useQuery({
    queryKey: [KEY, 'summary'],
    queryFn: () => apiGet<PlatformSummary>('/platform/summary/'),
  })
}

export function usePlatformOrganizations(params: { search?: string; status?: string }) {
  const query: Record<string, string> = {}
  if (params.search) query.search = params.search
  if (params.status) query.status = params.status
  return useQuery({
    queryKey: [KEY, 'organizations', params],
    queryFn: () =>
      apiGet<Paginated<PlatformOrganization>>('/platform/organizations/', { params: query }),
  })
}

export function usePlatformOrganization(id: string | undefined) {
  return useQuery({
    queryKey: [KEY, 'organizations', id],
    queryFn: () => apiGet<PlatformOrganization>(`/platform/organizations/${id}/`),
    enabled: Boolean(id),
  })
}

export function usePlatformPlans() {
  return useQuery({
    queryKey: [KEY, 'plans'],
    queryFn: () => apiGet<Paginated<PlatformPlan>>('/platform/plans/'),
  })
}

/** Invalidates everything under `platform` — the counts move with the rows. */
function useRefreshPlatform() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: [KEY] })
}

export function useProvisionOrganization() {
  const refresh = useRefreshPlatform()
  return useMutation({
    mutationFn: (body: ProvisionRequest) =>
      apiPost<ProvisionedOrganization>('/platform/organizations/', body),
    onSuccess: refresh,
  })
}

export function useChangePlan(id: string) {
  const refresh = useRefreshPlatform()
  return useMutation({
    mutationFn: (body: { plan: string; reason?: string; force?: boolean }) =>
      apiPost<PlatformOrganization>(`/platform/organizations/${id}/change-plan/`, body),
    onSuccess: refresh,
  })
}

export function useSetSubscriptionStatus(id: string) {
  const refresh = useRefreshPlatform()
  return useMutation({
    mutationFn: (body: { status: string; reason?: string }) =>
      apiPost<PlatformOrganization>(
        `/platform/organizations/${id}/subscription-status/`,
        body,
      ),
    onSuccess: refresh,
  })
}

export function useResendInvitation(id: string) {
  const refresh = useRefreshPlatform()
  return useMutation({
    mutationFn: () =>
      apiPost<InvitationResend>(`/platform/organizations/${id}/resend-invitation/`),
    onSuccess: refresh,
  })
}

export function useSeatOverride(id: string) {
  const refresh = useRefreshPlatform()
  return useMutation({
    // `employee_limit: null` defers to the plan again, which is why the field
    // is nullable rather than absent — omitting it would read as "no change".
    mutationFn: (body: { employee_limit: number | null; reason: string }) =>
      apiPost<PlatformOrganization>(`/platform/organizations/${id}/seat-override/`, body),
    onSuccess: refresh,
  })
}
