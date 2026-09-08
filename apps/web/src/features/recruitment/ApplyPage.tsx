/**
 * The public application form: /apply/:token.
 *
 * Bare — no shell, no navigation, no session. Whoever holds the link sees the
 * posting and the questions the job asks, and nothing else about the pipeline.
 * The token is the only credential, and the server decides everything: which
 * job it names, whether it is still open, which answers are acceptable.
 *
 * The questions come from the server (`fields`), not from this file, so a job
 * that asks something extra shows it here without a deploy. A submission id is
 * minted once per page load and re-sent on retry, which is what lets a slow
 * network and an impatient thumb produce one application instead of two.
 */

import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import axios from 'axios'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Checkbox, Select, TextArea, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { LoadingBlock } from '@/components/ui/States'
import { useBranding } from '@/lib/branding'
import { API_BASE } from '@/lib/api'
import type { ApplicationFieldSpec, PublicJobPosting } from '@/lib/types'

/*
 * Talks to the API without the authenticated client: no bearer token, no
 * refresh cookie, no session-lost handler. A candidate must never be bounced
 * to the sign-in page, and a stale HR session in this browser must never be
 * attached to their submission.
 */
export const anonymousClient = axios.create({ baseURL: API_BASE, withCredentials: false })
const anonymous = anonymousClient

function newSubmissionId(): string {
  const cryptoObj = globalThis.crypto
  if (cryptoObj && 'randomUUID' in cryptoObj) return cryptoObj.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

type Answers = Record<string, string>

export function ApplyPage() {
  const branding = useBranding()
  const { token = '' } = useParams<{ token: string }>()
  const [submissionId] = useState(newSubmissionId)
  const [answers, setAnswers] = useState<Answers>({})
  const [resume, setResume] = useState<File | null>(null)
  const [consent, setConsent] = useState(false)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  const posting = useQuery({
    queryKey: ['public', 'apply', token],
    queryFn: async () => (await anonymous.get<PublicJobPosting>(`/public/apply/${token}/`)).data,
    retry: false,
    enabled: Boolean(token),
  })

  const submit = useMutation({
    mutationFn: async () => {
      // JSON when there is nothing to upload; multipart when there is. The
      // server accepts both shapes on the same endpoint.
      if (resume) {
        const form = new FormData()
        form.append('submission_id', submissionId)
        form.append('answers', JSON.stringify(answers))
        form.append('consent', String(consent))
        form.append('resume', resume)
        const response = await anonymous.post<{ reference: string; job_title: string }>(
          `/public/apply/${token}/`,
          form,
          { headers: { 'Content-Type': undefined as unknown as string } },
        )
        return response.data
      }
      const response = await anonymous.post<{ reference: string; job_title: string }>(
        `/public/apply/${token}/`,
        { submission_id: submissionId, answers, consent },
      )
      return response.data
    },
    onError: (error: unknown) => {
      if (axios.isAxiosError(error) && error.response) {
        const data = error.response.data as {
          error?: { message?: string; details?: Record<string, unknown> }
        } & Record<string, unknown>
        const details = (data.error?.details ?? data) as Record<string, unknown>
        const nextErrors: Record<string, string> = {}
        for (const [key, value] of Object.entries(details)) {
          if (key === 'error') continue
          nextErrors[key] = Array.isArray(value) ? String(value[0]) : String(value)
        }
        setFieldErrors(nextErrors)
        setFormError(
          nextErrors.job ??
            nextErrors.consent ??
            data.error?.message ??
            (error.response.status === 429
              ? 'Too many attempts from this connection. Please try again in a little while.'
              : 'Please check the highlighted answers and try again.'),
        )
      } else {
        setFormError('Could not reach the server. Please check your connection and try again.')
      }
    },
  })

  const requiredMissing = useMemo(() => {
    const fields = posting.data?.fields ?? []
    return fields.filter((f) => f.required && !(answers[f.key] ?? '').trim()).map((f) => f.key)
  }, [posting.data, answers])

  const set = (key: string, value: string) => {
    setAnswers((prev) => ({ ...prev, [key]: value }))
    setFieldErrors((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  // The logo's lime — the same explicit accent every branded surface uses;
  // it reads identically over the blue band and on either theme's canvas.
  const LIME = '#A6CE39'

  return (
    <div className="min-h-screen bg-canvas px-4 py-8 sm:py-10">
      <div className="mx-auto w-full max-w-2xl motion-safe:animate-slide-up">
        {/* the clinic's face for candidates: wordmark, careers eyebrow, role */}
        <div className="relative mb-6 overflow-hidden rounded-2xl bg-gradient-to-br from-brand to-brand-hover p-6 text-white shadow-card sm:p-8">
          <div
            aria-hidden
            className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full border-[10px] motion-safe:animate-float-slow"
            style={{ borderColor: `${LIME}55` }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute -bottom-24 -left-16 h-48 w-48 rounded-full"
            style={{ backgroundColor: `${LIME}14` }}
          />
          <span className="relative flex items-center gap-2.5">
            {branding.logo ? (
              <img
                src={branding.logo}
                alt=""
                className="h-8 w-8 shrink-0 rounded-lg bg-white/90 object-contain p-0.5"
              />
            ) : (
              <span
                aria-hidden
                className="inline-block h-[13px] w-[13px] shrink-0 rounded-full border-[3.5px]"
                style={{ borderColor: LIME }}
              />
            )}
            <span className="text-lg font-extrabold uppercase tracking-tight">
              {branding.name}
            </span>
          </span>
          {branding.legal_name && branding.legal_name !== branding.name && (
            <p className="relative mt-0.5 text-2xs font-medium uppercase tracking-[0.26em] text-white/70">
              {branding.legal_name}
            </p>
          )}
          <p
            className="relative mt-5 text-2xs font-semibold uppercase tracking-[0.22em]"
            style={{ color: LIME }}
          >
            Careers
          </p>
          <h1 className="relative mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
            {posting.data?.title ?? 'Job application'}
          </h1>
          <div
            aria-hidden
            className="absolute inset-x-0 bottom-0 h-1"
            style={{ backgroundImage: `linear-gradient(to right, ${LIME}, ${LIME}66, transparent)` }}
          />
        </div>

        {posting.isLoading && <LoadingBlock label="Loading the position" />}

        {posting.isError && (
          <Banner tone="danger" title="This application link is not valid">
            The link may have been copied incompletely, or the position may have been removed.
            Please contact the person who shared it with you.
          </Banner>
        )}

        {posting.data && submit.data && (
          <div className="relative space-y-4 overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-card">
            <div
              aria-hidden
              className="absolute inset-x-0 top-0 h-1"
              style={{ backgroundImage: `linear-gradient(to right, ${LIME}, ${LIME}66, transparent)` }}
            />
            <Banner tone="success" title="Application received">
              Thank you for applying for <strong>{submit.data.job_title}</strong>. Your reference is{' '}
              <strong data-testid="reference">{submit.data.reference}</strong>. A confirmation has
              been sent to your email address.
            </Banner>
            <p className="text-sm text-ink-muted">
              Our recruitment team will review your application and contact you about the next
              steps. Please quote your reference in any correspondence.
            </p>
          </div>
        )}

        {posting.data && !submit.data && !posting.data.accepts_applications && (
          <Banner tone="warning" title="This position is not accepting applications">
            Thank you for your interest. This opening has closed, but we would be glad to hear from
            you about future roles.
          </Banner>
        )}

        {posting.data && !submit.data && posting.data.accepts_applications && (
          <form
            noValidate
            className="space-y-6"
            onSubmit={(event) => {
              event.preventDefault()
              setFormError(null)
              if (requiredMissing.length) {
                setFieldErrors(
                  Object.fromEntries(requiredMissing.map((k) => [k, 'This answer is required.'])),
                )
                setFormError('Please fill in the required answers.')
                return
              }
              if (!consent) {
                setFormError('Please confirm the declaration to apply.')
                return
              }
              submit.mutate()
            }}
          >
            <section className="space-y-3 rounded-2xl border border-line bg-surface p-6 shadow-card">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: LIME }}
                />
                About the role
              </h2>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-muted">
                <span>{posting.data.department}</span>
                {posting.data.location && <span>· {posting.data.location}</span>}
                <span>· {posting.data.employment_type.replace(/_/g, ' ')}</span>
              </div>
              {posting.data.description && (
                <p className="whitespace-pre-line text-sm text-ink">{posting.data.description}</p>
              )}
              {posting.data.requirements && (
                <div>
                  <h2 className="mb-1 text-sm font-semibold text-ink">Requirements</h2>
                  <p className="whitespace-pre-line text-sm text-ink-muted">
                    {posting.data.requirements}
                  </p>
                </div>
              )}
            </section>

            <section className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-card">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: LIME }}
                />
                Your details
              </h2>
              {formError && (
                <Banner tone="danger" title="Not submitted">
                  {formError}
                </Banner>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                {posting.data.fields.map((field) =>
                  field.type === 'file' ? (
                    <FileControl
                      key={field.key}
                      spec={field}
                      file={resume}
                      error={fieldErrors[field.key] ?? fieldErrors.resume}
                      onChange={setResume}
                    />
                  ) : (
                    <FieldControl
                      key={field.key}
                      spec={field}
                      value={answers[field.key] ?? ''}
                      error={fieldErrors[field.key]}
                      onChange={(value) => set(field.key, value)}
                    />
                  ),
                )}
              </div>
            </section>

            <section className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-card">
              <Checkbox
                label={posting.data.consent_text}
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
                required
              />
              <Button type="submit" loading={submit.isPending} className="w-full sm:w-auto">
                Submit application
              </Button>
              <p className="text-xs text-ink-subtle">
                Your information is used only to consider this application and to contact you about
                it.
              </p>
            </section>
          </form>
        )}
      </div>
    </div>
  )
}

/**
 * The résumé upload. Client-side size/type check for a fast answer; the
 * server re-checks both — this is convenience, not the guard.
 */
function FileControl({
  spec,
  file,
  error,
  onChange,
}: {
  spec: ApplicationFieldSpec
  file: File | null
  error?: string
  onChange: (file: File | null) => void
}) {
  const [localError, setLocalError] = useState<string | undefined>()
  return (
    <div className="space-y-1.5 sm:col-span-2">
      <label className="text-sm font-medium text-ink">
        {spec.label}
        {spec.required && <span className="ml-0.5 text-danger">*</span>}
      </label>
      <input
        type="file"
        accept=".pdf,.doc,.docx"
        aria-label={spec.label}
        className="block w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink file:mr-3 file:rounded-md file:border-0 file:bg-canvas file:px-3 file:py-1.5 file:text-sm"
        onChange={(event) => {
          const next = event.target.files?.[0] ?? null
          if (next && next.size > 5 * 1024 * 1024) {
            setLocalError('The file is larger than 5 MB.')
            onChange(null)
            return
          }
          setLocalError(undefined)
          onChange(next)
        }}
      />
      {file && <p className="text-xs text-ink-muted">{file.name}</p>}
      {(localError ?? error) && <p className="text-xs text-danger">{localError ?? error}</p>}
      {spec.help_text && !localError && !error && (
        <p className="text-xs text-ink-subtle">{spec.help_text}</p>
      )}
    </div>
  )
}

function FieldControl({
  spec,
  value,
  error,
  onChange,
}: {
  spec: ApplicationFieldSpec
  value: string
  error?: string
  onChange: (value: string) => void
}) {
  const common = {
    label: spec.label,
    required: spec.required,
    error,
    description: spec.help_text || undefined,
  }
  const wide = spec.type === 'textarea'
  const containerClassName = wide ? 'sm:col-span-2' : undefined

  if (spec.type === 'select') {
    return (
      <Select
        {...common}
        containerClassName={containerClassName}
        placeholder="Select…"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        options={spec.options.map((option) => ({ value: option, label: option }))}
      />
    )
  }
  if (spec.type === 'textarea') {
    return (
      <TextArea
        {...common}
        containerClassName={containerClassName}
        rows={4}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  const inputType =
    spec.type === 'email'
      ? 'email'
      : spec.type === 'phone'
        ? 'tel'
        : spec.type === 'number'
          ? 'number'
          : spec.type === 'date'
            ? 'date'
            : spec.type === 'url'
              ? 'url'
              : 'text'
  return (
    <TextInput
      {...common}
      containerClassName={containerClassName}
      type={inputType}
      inputMode={spec.type === 'phone' ? 'tel' : spec.type === 'number' ? 'decimal' : undefined}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}
