/**
 * Candidate import data access.
 *
 * Talks only to the Phase 3–5 endpoints. There is no client-side parsing, no
 * client-side deduplication and no second import path: the backend is the
 * source of truth for what a file contains and what happens to it, and the
 * screen renders what it is told.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiGet, apiPost, http } from './api'
import type { ImportRow,
  ImportBatch,
  ImportCommitResult,
  ImportErrorReport,
  ImportPlatform,
  UUID,
} from './types'

const BASE = '/candidate-imports/'

export const importKeys = {
  all: ['candidate-imports'] as const,
  platforms: ['candidate-imports', 'platforms'] as const,
  batch: (id: UUID | undefined) => ['candidate-imports', 'detail', id ?? 'none'] as const,
  errors: (id: UUID | undefined) => ['candidate-imports', 'detail', id ?? 'none', 'errors'] as const,
}

/**
 * Which platforms can be imported from, and why the others cannot.
 *
 * Cached for a while: availability changes when a partner agreement is signed,
 * not between page loads.
 */
export function useImportPlatforms() {
  return useQuery({
    queryKey: importKeys.platforms,
    queryFn: () => apiGet<ImportPlatform[]>(`${BASE}platforms/`),
    staleTime: 15 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  })
}

export interface UploadInput {
  platform: string
  job_opening: UUID
  file: File
  /** Header -> canonical field, when the export's columns are unrecognised. */
  column_override?: Record<string, string>
}

/**
 * Upload, parse and preview. Writes no candidates.
 *
 * Multipart through the shared axios client, with the JSON content type nulled
 * so the browser writes its own boundary — the same idiom the document upload
 * uses in lifecycleQueries.
 */
export function useUploadImport() {
  return useMutation({
    mutationFn: (input: UploadInput) => {
      const form = new FormData()
      form.append('platform', input.platform)
      form.append('job_opening', input.job_opening)
      form.append('file', input.file)
      if (input.column_override) {
        form.append('column_override', JSON.stringify(input.column_override))
      }
      return http
        .post<ImportBatch>(BASE, form, {
          headers: { 'Content-Type': undefined as unknown as string },
        })
        .then((response) => response.data)
    },
  })
}

export interface AttestInput {
  batchId: UUID
  legal_basis: string
  legal_basis_note: string
  attestation_text: string
}

/** Record the lawful basis. The backend refuses to commit without it. */
export function useAttestImport() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ batchId, ...body }: AttestInput) =>
      apiPost<ImportBatch>(`${BASE}${batchId}/attest/`, body),
    onSuccess: (batch) => {
      client.setQueryData(importKeys.batch(batch.id), batch)
    },
  })
}

/**
 * Write the staged rows into the pipeline.
 *
 * Invalidates candidates AND applications: one import changes both lists, and
 * a stale candidate list after an import is how someone concludes it silently
 * failed.
 */
export function useCommitImport() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (batchId: UUID) =>
      apiPost<ImportCommitResult>(`${BASE}${batchId}/commit/`),
    onSuccess: (result) => {
      client.setQueryData(importKeys.batch(result.batch.id), result.batch)
      client.invalidateQueries({ queryKey: ['candidates'] })
      client.invalidateQueries({ queryKey: ['applications'] })
      client.invalidateQueries({ queryKey: importKeys.all })
    },
  })
}

/** The failed-row report. JSON, so nothing here can become a spreadsheet formula. */
export function useImportErrors(batchId: UUID | undefined, enabled = false) {
  return useQuery({
    queryKey: importKeys.errors(batchId),
    queryFn: () => apiGet<ImportErrorReport>(`${BASE}${batchId}/errors/`),
    enabled: Boolean(batchId) && enabled,
  })
}

/**
 * Save the failed-row report to a file.
 *
 * JSON, deliberately, and not CSV. A CSV of candidate-derived values is a
 * formula-injection vector against whoever opens it, and this file is destined
 * for a spreadsheet on someone's laptop. The backend returns codes rather than
 * cell values for the same reason.
 *
 * Fetched through the authenticated client rather than an <a download> href:
 * the access token lives in memory, so a bare link would 401.
 */
export function useCommitResultDownload() {
  return (batchId: UUID, filename: string) =>
    apiGet<ImportErrorReport>(`${BASE}${batchId}/errors/`).then((report) => {
      const blob = new Blob([JSON.stringify(report, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `import-errors-${filename || batchId}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    })
}


/** Refetch a staged batch after a row edit (the page holds it in state). */
export function fetchImportBatch(batchId: UUID) {
  return apiGet<ImportBatch>(`${BASE}${batchId}/`)
}

/** `POST rows/update` — edit one staged row's cells; dedup re-resolves. */
export function useUpdateImportRow() {
  return useMutation({
    mutationFn: ({ batchId, row_number, raw }: {
      batchId: UUID
      row_number: number
      raw: Record<string, string>
    }) => apiPost<ImportRow>(`${BASE}${batchId}/rows/update/`, { row_number, raw }),
  })
}

/** `POST rows/add` — hand-add a candidate row to the staged batch. */
export function useAddImportRow() {
  return useMutation({
    mutationFn: ({ batchId, raw }: { batchId: UUID; raw: Record<string, string> }) =>
      apiPost<ImportRow>(`${BASE}${batchId}/rows/add/`, { raw }),
  })
}

/** `POST rows/remove` — drop a staged row before commit. Staging only. */
export function useRemoveImportRow() {
  return useMutation({
    mutationFn: ({ batchId, row_number }: { batchId: UUID; row_number: number }) =>
      apiPost<void>(`${BASE}${batchId}/rows/remove/`, { row_number }),
  })
}
