/**
 * Lifecycle hooks: profile, documents, probation, onboarding, letters,
 * accounts and assets.
 *
 * Kept alongside `queries.ts` rather than inside it because this is a distinct
 * surface, but the conventions are identical: one place declares every URL, and
 * no component builds a path. That matters here in particular because several
 * routes are gated on a different action from the HTTP verb they use —
 * `/write-off/` needs DELETE, `/decide/` needs DECIDE — and a component
 * assembling paths by hand would eventually post to the wrong one.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost, http, type Paginated, toRelative } from './api'
import type { ListParams } from './queries'
import type {
  Asset,
  AssetAllocation,
  AssetCategory,
  CompanyEmailAccount,
  DocumentType,
  EmployeeDocument,
  EmployeeLetter,
  EmployeeOnboarding,
  EmployeeProfile,
  LetterTemplate,
  OnboardingItem,
  ProbationBuckets,
  ProbationReview,
  UUID,
} from './types'

function clean(params: ListParams): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Record<string, string>
}

export const lifecycleKeys = {
  profile: (id: UUID) => ['employees', 'detail', id, 'profile'] as const,
  documentTypes: ['reference', 'document-types'] as const,
  documents: (params?: unknown) => ['employee-documents', params ?? {}] as const,
  probation: (params?: unknown) => ['probation-reviews', params ?? {}] as const,
  probationUpcoming: ['probation-reviews', 'upcoming'] as const,
  onboarding: (params?: unknown) => ['onboarding', params ?? {}] as const,
  onboardingOverdue: ['onboarding', 'overdue'] as const,
  letters: (params?: unknown) => ['letters', params ?? {}] as const,
  letterTemplates: ['reference', 'letter-templates'] as const,
  accounts: (params?: unknown) => ['company-accounts', params ?? {}] as const,
  assets: (params?: unknown) => ['assets', params ?? {}] as const,
  assetCategories: ['reference', 'asset-categories'] as const,
  allocations: (params?: unknown) => ['asset-allocations', params ?? {}] as const,
}

/**
 * Invalidates everything a profile page shows.
 *
 * A single action on the profile — verifying a document, ticking a checklist
 * item, returning an asset — changes several sections at once, and refreshing
 * only the one that was clicked leaves the rest visibly stale.
 */
function useProfileInvalidator(employeeId?: UUID) {
  const queryClient = useQueryClient()
  return () => {
    if (employeeId) {
      void queryClient.invalidateQueries({ queryKey: lifecycleKeys.profile(employeeId) })
    }
    for (const key of [
      'employee-documents', 'probation-reviews', 'onboarding',
      'letters', 'company-accounts', 'asset-allocations', 'assets', 'employees',
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }
}

/* -------------------------------------------------------------- profile */

export function useEmployeeProfile(id: UUID | undefined) {
  return useQuery({
    queryKey: lifecycleKeys.profile(id ?? 'none'),
    queryFn: () => apiGet<EmployeeProfile>(`/employees/${id}/profile/`),
    enabled: Boolean(id),
  })
}

export function useChangeEmployeeStatus(id: UUID) {
  const invalidate = useProfileInvalidator(id)
  return useMutation({
    mutationFn: (body: { status: string; reason?: string; effective_date?: string }) =>
      apiPost(`/employees/${id}/status/`, body),
    onSuccess: invalidate,
  })
}

/* ------------------------------------------------------------ documents */

export function useDocumentTypes() {
  return useQuery({
    queryKey: lifecycleKeys.documentTypes,
    queryFn: () => apiGet<DocumentType[]>('/document-types/'),
    staleTime: 15 * 60 * 1000,
  })
}

export function useEmployeeDocuments(params: ListParams = {}) {
  return useQuery({
    queryKey: lifecycleKeys.documents(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<EmployeeDocument>>(toRelative(params.cursor))
        : apiGet<Paginated<EmployeeDocument>>('/employee-documents/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useUploadDocument(employeeId: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return useMutation({
    mutationFn: (input: {
      document_type: UUID
      file: File
      issue_date?: string
      expires_on?: string
      notes?: string
    }) => {
      // multipart, because a file cannot travel as JSON. The axios instance
      // sets a JSON content type by default, so it is cleared here and the
      // browser supplies the multipart boundary.
      const form = new FormData()
      form.append('employee', employeeId)
      form.append('document_type', input.document_type)
      form.append('file', input.file)
      if (input.issue_date) form.append('issue_date', input.issue_date)
      if (input.expires_on) form.append('expires_on', input.expires_on)
      if (input.notes) form.append('notes', input.notes)
      return http
        .post<EmployeeDocument>('/employee-documents/', form, {
          headers: { 'Content-Type': undefined as unknown as string },
        })
        .then((response) => response.data)
    },
    onSuccess: invalidate,
  })
}

export function useDocumentActions(employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return {
    verify: useMutation({
      mutationFn: (id: UUID) => apiPost<EmployeeDocument>(`/employee-documents/${id}/verify/`),
      onSuccess: invalidate,
    }),
    reject: useMutation({
      mutationFn: (input: { id: UUID; reason: string }) =>
        apiPost<EmployeeDocument>(`/employee-documents/${input.id}/reject/`, {
          reason: input.reason,
        }),
      onSuccess: invalidate,
    }),
  }
}

/**
 * Download a stored file.
 *
 * Fetched as a blob through the authenticated client rather than opened as a
 * URL, because the bearer token lives in memory and a bare `window.open` would
 * arrive at the API unauthenticated.
 */
export async function downloadFile(url: string, filename: string) {
  const response = await http.get(url, { responseType: 'blob' })
  const href = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(href)
}

/* ------------------------------------------------------------ probation */

export function useProbationReviews(params: ListParams = {}) {
  return useQuery({
    queryKey: lifecycleKeys.probation(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<ProbationReview>>(toRelative(params.cursor))
        : apiGet<Paginated<ProbationReview>>('/probation-reviews/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function usePendingProbationReviews(enabled = true) {
  return useQuery({
    queryKey: ['probation-reviews', 'pending'],
    queryFn: () => apiGet<Paginated<ProbationReview>>('/probation-reviews/pending/'),
    enabled,
  })
}

export function useUpcomingProbations(enabled = true) {
  return useQuery({
    queryKey: lifecycleKeys.probationUpcoming,
    queryFn: () => apiGet<ProbationBuckets>('/probation-reviews/upcoming/'),
    enabled,
  })
}

export function useProbationActions(reviewId: UUID, employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return {
    assess: useMutation({
      mutationFn: (body: {
        recommendation: string
        performance_rating?: number
        reliability_rating?: number
        role_specific_rating?: number
        strengths?: string
        areas_for_improvement?: string
        notes?: string
      }) => apiPost<ProbationReview>(`/probation-reviews/${reviewId}/assess/`, body),
      onSuccess: invalidate,
    }),
    // The employment decision. Gated on DECIDE, which only HR holds.
    decide: useMutation({
      mutationFn: (body: { decision: string; rationale?: string; extended_to?: string }) =>
        apiPost<ProbationReview>(`/probation-reviews/${reviewId}/decide/`, body),
      onSuccess: invalidate,
    }),
  }
}

/* ----------------------------------------------------------- onboarding */

export function useOnboardings(params: ListParams = {}) {
  return useQuery({
    queryKey: lifecycleKeys.onboarding(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<EmployeeOnboarding>>(toRelative(params.cursor))
        : apiGet<Paginated<EmployeeOnboarding>>('/onboarding/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useOverdueOnboardings(enabled = true) {
  return useQuery({
    queryKey: lifecycleKeys.onboardingOverdue,
    queryFn: () => apiGet<EmployeeOnboarding[]>('/onboarding/overdue/'),
    enabled,
  })
}

export function useOnboardingItemActions(employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return {
    complete: useMutation({
      mutationFn: (input: { id: UUID; notes?: string; document?: UUID; file?: File }) => {
        if (input.file) {
          const form = new FormData()
          form.append('file', input.file)
          if (input.notes) form.append('notes', input.notes)
          return http
            .post<OnboardingItem>(`/onboarding-items/${input.id}/complete/`, form, {
              headers: { 'Content-Type': undefined as unknown as string },
            })
            .then((response) => response.data)
        }
        return apiPost<OnboardingItem>(`/onboarding-items/${input.id}/complete/`, {
          notes: input.notes ?? '',
          ...(input.document ? { document: input.document } : {}),
        })
      },
      onSuccess: invalidate,
    }),
    waive: useMutation({
      mutationFn: (input: { id: UUID; reason: string }) =>
        apiPost<OnboardingItem>(`/onboarding-items/${input.id}/waive/`, {
          reason: input.reason,
        }),
      onSuccess: invalidate,
    }),
    reopen: useMutation({
      mutationFn: (input: { id: UUID; reason?: string }) =>
        apiPost<OnboardingItem>(`/onboarding-items/${input.id}/reopen/`, {
          reason: input.reason ?? '',
        }),
      onSuccess: invalidate,
    }),
  }
}

/* -------------------------------------------------------------- letters */

export function useLetterTemplates() {
  return useQuery({
    queryKey: lifecycleKeys.letterTemplates,
    queryFn: () => apiGet<LetterTemplate[]>('/letter-templates/'),
    staleTime: 15 * 60 * 1000,
  })
}

export function useGenerateLetter(employeeId: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return useMutation({
    mutationFn: (body: { letter_type: string; template?: UUID }) =>
      apiPost<EmployeeLetter>('/letters/', { employee: employeeId, ...body }),
    onSuccess: invalidate,
  })
}

/* ------------------------------------------------------------- accounts */

export function useRecordCompanyAccount(employeeId: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return useMutation({
    // No credential is ever sent. The API refuses a payload carrying one, and
    // there is no field here that could carry it.
    mutationFn: (body: {
      email_address: string
      provider: string
      external_account_id?: string
      notes?: string
    }) => apiPost<CompanyEmailAccount>('/company-accounts/', { employee: employeeId, ...body }),
    onSuccess: invalidate,
  })
}

export function useAccountActions(accountId: UUID, employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return {
    provision: useMutation({
      mutationFn: (body: { external_account_id?: string }) =>
        apiPost<CompanyEmailAccount>(`/company-accounts/${accountId}/provision/`, body),
      onSuccess: invalidate,
    }),
    suspend: useMutation({
      mutationFn: (body: { reason?: string }) =>
        apiPost<CompanyEmailAccount>(`/company-accounts/${accountId}/suspend/`, body),
      onSuccess: invalidate,
    }),
  }
}

/* --------------------------------------------------------------- assets */

export function useAssetCategories() {
  return useQuery({
    queryKey: lifecycleKeys.assetCategories,
    queryFn: () => apiGet<AssetCategory[]>('/asset-categories/'),
    staleTime: 15 * 60 * 1000,
  })
}

export function useAssets(params: ListParams = {}) {
  return useQuery({
    queryKey: lifecycleKeys.assets(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<Asset>>(toRelative(params.cursor))
        : apiGet<Paginated<Asset>>('/assets/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useAssetAllocations(params: ListParams = {}) {
  return useQuery({
    queryKey: lifecycleKeys.allocations(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<AssetAllocation>>(toRelative(params.cursor))
        : apiGet<Paginated<AssetAllocation>>('/asset-allocations/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

/** The simple register form: name + Asset ID, everything else optional. */
export interface AssetPayload {
  asset_tag: string
  name: string
  serial_number?: string
  location?: UUID | null
  purchase_date?: string | null
  notes?: string
}

export function useCreateAsset() {
  const invalidate = useProfileInvalidator()
  return useMutation({
    mutationFn: (body: AssetPayload) => apiPost<Asset>('/assets/', body),
    onSuccess: invalidate,
  })
}

export function useUpdateAsset() {
  const invalidate = useProfileInvalidator()
  return useMutation({
    mutationFn: ({ id, ...body }: AssetPayload & { id: UUID }) =>
      apiPatch<Asset>(`/assets/${id}/`, body),
    onSuccess: invalidate,
  })
}

/** Several assets into one pair of hands — one request, all-or-nothing. */
export function useBulkAllocate(employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return useMutation({
    mutationFn: (body: { employee: UUID; assets: UUID[]; notes?: string }) =>
      apiPost<AssetAllocation[]>('/asset-allocations/bulk/', body),
    onSuccess: invalidate,
  })
}

export function useAllocateAsset(employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return useMutation({
    mutationFn: (body: {
      asset: UUID
      employee: UUID
      condition?: string
      notes?: string
      expected_return_date?: string
    }) => apiPost<AssetAllocation>('/asset-allocations/', body),
    onSuccess: invalidate,
  })
}

export function useAllocationActions(employeeId?: UUID) {
  const invalidate = useProfileInvalidator(employeeId)
  return {
    returnAsset: useMutation({
      mutationFn: (input: {
        id: UUID
        condition?: string
        notes?: string
        to_maintenance?: boolean
      }) =>
        apiPost<AssetAllocation>(`/asset-allocations/${input.id}/return/`, {
          condition: input.condition ?? 'good',
          notes: input.notes ?? '',
          to_maintenance: input.to_maintenance ?? false,
        }),
      onSuccess: invalidate,
    }),
    // Accepting that company property is gone. Gated on DELETE, not EDIT.
    writeOff: useMutation({
      mutationFn: (input: { id: UUID; reason: string }) =>
        apiPost<AssetAllocation>(`/asset-allocations/${input.id}/write-off/`, {
          reason: input.reason,
        }),
      onSuccess: invalidate,
    }),
  }
}
