/**
 * Interview feedback, rendered from the stage's configured form.
 *
 * The fields come from `/feedback-forms/` — key, label, kind, required, and
 * the choice list for `choice` fields. A clinical scorecard and an operations
 * scorecard are different rows in that table, and this one component renders
 * both. There is no clinical form component.
 *
 * `recommendation` is ADVISORY. The backend records it on the feedback and
 * never lets it move the application by itself; the stage decision is a
 * separate act. The copy says so, because an interviewer who believes their
 * "No hire" rejected someone will not understand what happens next.
 */

import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Modal'
import { Field, Select, TextArea } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { useToast } from '@/components/ui/Toast'
import { LoadingBlock } from '@/components/ui/States'
import { useFeedbackForms, useSubmitFeedback } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import type { FeedbackField, Interview, Recommendation, UUID } from '@/lib/types'

type AnswerValue = string | number | boolean

const RECOMMENDATIONS: Array<{ value: Recommendation; label: string }> = [
  { value: 'strong_hire', label: 'Strong hire' },
  { value: 'hire', label: 'Hire' },
  { value: 'hold', label: 'Hold' },
  { value: 'no hire', label: 'No hire' },
]

function RatingInput({
  field,
  value,
  onChange,
  error,
}: {
  field: FeedbackField
  value: AnswerValue | undefined
  onChange: (value: number) => void
  error?: string
}) {
  return (
    <Field label={field.label} required={field.is_required} error={error}>
      {({ describedBy, invalid }) => (
        // A radio group, not five buttons: arrow keys move between options and
        // the group announces as one control with a current value.
        <div
          role="radiogroup"
          aria-label={field.label}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className="flex gap-1.5"
        >
          {[1, 2, 3, 4, 5].map((score) => {
            const selected = value === score
            return (
              <button
                key={score}
                type="button"
                role="radio"
                aria-checked={selected}
                aria-label={`${score} out of 5`}
                onClick={() => onChange(score)}
                className={[
                  'h-9 w-9 rounded-lg border text-sm font-semibold transition-colors',
                  selected
                    ? 'border-brand bg-brand text-white'
                    : 'border-line bg-surface text-ink-muted hover:border-brand/40 hover:text-ink',
                ].join(' ')}
              >
                {score}
              </button>
            )
          })}
        </div>
      )}
    </Field>
  )
}

export function FeedbackDrawer({
  open,
  onClose,
  interview,
  formId,
  onSubmitted,
}: {
  open: boolean
  onClose: () => void
  interview: Interview
  formId: UUID | null
  onSubmitted?: () => void
}) {
  const toast = useToast()
  const forms = useFeedbackForms()
  const submit = useSubmitFeedback(interview.id)

  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({})
  const [recommendation, setRecommendation] = useState<Recommendation | ''>('')
  const [strengths, setStrengths] = useState('')
  const [concerns, setConcerns] = useState('')
  const [overallRating, setOverallRating] = useState<number | ''>('')
  const [touched, setTouched] = useState(false)
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({})

  const form = useMemo(
    () => (forms.data?.data ?? []).find((candidate) => candidate.id === formId),
    [forms.data, formId],
  )

  const fields = useMemo(
    () => [...(form?.fields_ ?? [])].sort((a, b) => a.order - b.order),
    [form],
  )

  const missing = fields.filter(
    (field) => field.is_required && (answers[field.key] === undefined || answers[field.key] === ''),
  )
  const ready = missing.length === 0 && recommendation !== ''

  function setAnswer(key: string, value: AnswerValue) {
    setAnswers((current) => ({ ...current, [key]: value }))
    setServerErrors(({ [key]: _removed, ...rest }) => rest)
  }

  function errorFor(field: FeedbackField) {
    if (serverErrors[field.key]) return serverErrors[field.key]
    if (touched && field.is_required && answers[field.key] === undefined) {
      return `${field.label} is required.`
    }
    return undefined
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      busy={submit.isPending}
      width="lg"
      title="Submit interview feedback"
      description={`${interview.candidate_name} — ${interview.stage_name}`}
      footer={
        <>
          <Button onClick={onClose} disabled={submit.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={submit.isPending}
            onClick={() => {
              setTouched(true)
              if (!ready) return
              setServerErrors({})
              submit.mutate(
                {
                  answers,
                  recommendation: recommendation as string,
                  strengths,
                  concerns,
                  ...(overallRating === '' ? {} : { overall_rating: overallRating }),
                },
                {
                  onSuccess: () => {
                    toast.success('Feedback submitted')
                    onClose()
                    onSubmitted?.()
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setServerErrors(error.fieldErrors)
                    toast.fromError(error, 'Feedback not submitted')
                  },
                },
              )
            }}
          >
            Submit feedback
          </Button>
        </>
      }
    >
      {forms.isLoading && formId ? (
        <LoadingBlock label="Loading the assessment form" />
      ) : formId && !form ? (
        <Banner tone="warning" title="The assessment form for this stage could not be loaded">
          The stage names a form you cannot read, or it has been retired. Ask an administrator to
          check the workflow configuration.
        </Banner>
      ) : (
        <div className="space-y-5">
          <Banner tone="info" title="Your recommendation is advisory">
            It is recorded against this interview and shown to the department head and HR Head. It
            does not move or close the application by itself — the stage decision is separate.
          </Banner>

          {/*
            A stage with no assessment form still takes feedback: the
            recommendation, rating, strengths and concerns below ARE the
            feedback, and the server accepts no structured answers for it. An
            HR round in a custom workflow usually has no form, and a verdict
            should not be unrecordable for want of a questionnaire.
          */}
          <div className="space-y-4">
            {form ? (
              <p className="text-sm font-semibold text-ink">{form.name}</p>
            ) : (
              <p className="text-sm text-ink-muted">
                No structured assessment form is attached to this stage — record your overall
                recommendation and notes below.
              </p>
            )}

            {fields.map((field) => {
              const error = errorFor(field)
              const value = answers[field.key]

              if (field.kind === 'rating_1_5') {
                return (
                  <RatingInput
                    key={field.id}
                    field={field}
                    value={value}
                    error={error}
                    onChange={(score) => setAnswer(field.key, score)}
                  />
                )
              }

              if (field.kind === 'boolean') {
                return (
                  <Select
                    key={field.id}
                    label={field.label}
                    required={field.is_required}
                    error={error}
                    value={value === undefined ? '' : String(value)}
                    onChange={(event) => setAnswer(field.key, event.target.value === 'true')}
                    placeholder="Select"
                    options={[
                      { value: 'true', label: 'Yes' },
                      { value: 'false', label: 'No' },
                    ]}
                  />
                )
              }

              if (field.kind === 'choice') {
                return (
                  <Select
                    key={field.id}
                    label={field.label}
                    required={field.is_required}
                    error={error}
                    value={value === undefined ? '' : String(value)}
                    onChange={(event) => setAnswer(field.key, event.target.value)}
                    placeholder="Select"
                    options={(field.choices ?? []).map((choice) => ({
                      value: choice,
                      label: choice,
                    }))}
                  />
                )
              }

              if (field.kind === 'score_0_100') {
                return (
                  <Field
                    key={field.id}
                    label={field.label}
                    required={field.is_required}
                    error={error}
                  >
                    {({ id, describedBy, invalid }) => (
                      <input
                        id={id}
                        type="number"
                        min={0}
                        max={100}
                        aria-describedby={describedBy}
                        aria-invalid={invalid || undefined}
                        value={value === undefined ? '' : String(value)}
                        onChange={(event) => setAnswer(field.key, Number(event.target.value))}
                        className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-base text-ink"
                      />
                    )}
                  </Field>
                )
              }

              return (
                <TextArea
                  key={field.id}
                  label={field.label}
                  required={field.is_required}
                  error={error}
                  rows={3}
                  value={value === undefined ? '' : String(value)}
                  onChange={(event) => setAnswer(field.key, event.target.value)}
                />
              )
            })}
          </div>

          <div className="space-y-4 border-t border-line pt-4">
            <Select
              label="Overall recommendation"
              required
              value={recommendation}
              onChange={(event) => setRecommendation(event.target.value as Recommendation)}
              placeholder="Select a recommendation"
              options={RECOMMENDATIONS}
              error={
                touched && recommendation === '' ? 'A recommendation is required.' : undefined
              }
            />
            <Select
              label="Overall rating"
              value={overallRating === '' ? '' : String(overallRating)}
              onChange={(event) =>
                setOverallRating(event.target.value === '' ? '' : Number(event.target.value))
              }
              placeholder="Optional"
              options={[1, 2, 3, 4, 5].map((score) => ({
                value: String(score),
                label: `${score} / 5`,
              }))}
            />
            <TextArea
              label="Strengths"
              rows={3}
              value={strengths}
              onChange={(event) => setStrengths(event.target.value)}
            />
            <TextArea
              label="Concerns"
              rows={3}
              value={concerns}
              onChange={(event) => setConcerns(event.target.value)}
            />
          </div>
        </div>
      )}
    </Drawer>
  )
}
