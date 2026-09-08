/**
 * Admin Panel mutations: organisation catalogues, roles, permission cells,
 * and workflow authoring.
 *
 * Every mutation invalidates the reference queries the rest of the app reads
 * (`queryKeys.departments` etc.), so a department created here appears in the
 * employee form's picker without a reload.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from './api'
import { queryKeys } from './queries'
import type { HiringWorkflow, Role, UUID } from './types'

/* ---------------------------------------------------------------- shared */

const CATALOGUE_PATHS = {
  departments: '/departments/',
  designations: '/designations/',
  locations: '/locations/',
  levels: '/levels/',
} as const

export type CatalogueKind = keyof typeof CATALOGUE_PATHS

const CATALOGUE_KEYS: Record<CatalogueKind, readonly unknown[]> = {
  departments: queryKeys.departments,
  designations: queryKeys.designations,
  locations: queryKeys.locations,
  levels: queryKeys.levels,
}

export function useCatalogueMutations(kind: CatalogueKind) {
  const queryClient = useQueryClient()
  const path = CATALOGUE_PATHS[kind]
  const invalidate = () => queryClient.invalidateQueries({ queryKey: CATALOGUE_KEYS[kind] })

  return {
    create: useMutation({
      mutationFn: (payload: Record<string, unknown>) => apiPost(path, payload),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: (input: { id: UUID; payload: Record<string, unknown> }) =>
        apiPatch(`${path}${input.id}/`, input.payload),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: UUID) => apiDelete(`${path}${id}/`),
      onSuccess: invalidate,
    }),
  }
}

/* ----------------------------------------------------------------- roles */

export interface PermissionCell {
  resource: string
  action: string
  scope: number
  is_customized?: boolean
}

export interface AccessCatalog {
  resources: string[]
  actions: string[]
  scopes: string[]
}

export function useAccessCatalog() {
  return useQuery({
    queryKey: ['access-catalog'],
    queryFn: () => apiGet<AccessCatalog>('/access-catalog/'),
    staleTime: Infinity,
  })
}

export function useRolePermissions(roleId: UUID | undefined) {
  return useQuery({
    queryKey: ['role-permissions', roleId],
    queryFn: () => apiGet<PermissionCell[]>(`/roles/${roleId}/permissions/`),
    enabled: Boolean(roleId),
  })
}

export function useRoleAdmin() {
  const queryClient = useQueryClient()
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.roles })
    void queryClient.invalidateQueries({ queryKey: ['role-permissions'] })
  }

  return {
    create: useMutation({
      mutationFn: (payload: Record<string, unknown>) => apiPost<Role>('/roles/', payload),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: (input: { id: UUID; payload: Record<string, unknown> }) =>
        apiPatch<Role>(`/roles/${input.id}/`, input.payload),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: UUID) => apiDelete(`/roles/${id}/`),
      onSuccess: invalidate,
    }),
    setPermissions: useMutation({
      mutationFn: (input: { id: UUID; cells: PermissionCell[] }) =>
        apiPost<PermissionCell[]>(`/roles/${input.id}/permissions/set/`, {
          cells: input.cells,
        }),
      onSuccess: invalidate,
    }),
  }
}

/* ------------------------------------------------------------- workflows */

export interface InterviewRoundInput {
  name: string
  role: string
  feedback_form?: UUID | null
}

export interface WorkflowAuthorInput {
  name: string
  description?: string
  department_kind?: string
  verification_role?: string
  interview_rounds: InterviewRoundInput[]
  recommendation_role?: string | null
}

export function useWorkflowAuthoring() {
  const queryClient = useQueryClient()
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['workflows'] })

  return {
    create: useMutation({
      mutationFn: (payload: WorkflowAuthorInput) =>
        apiPost<HiringWorkflow>('/workflows/', payload),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: (input: { id: UUID; payload: Partial<WorkflowAuthorInput> }) =>
        apiPatch<HiringWorkflow>(`/workflows/${input.id}/`, input.payload),
      onSuccess: invalidate,
    }),
    publish: useMutation({
      mutationFn: (id: UUID) => apiPost<HiringWorkflow>(`/workflows/${id}/publish/`),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: UUID) => apiDelete(`/workflows/${id}/`),
      onSuccess: invalidate,
    }),
  }
}
