/**
 * Form controls.
 *
 * Every control is wrapped by `Field`, which owns the label/description/error
 * wiring: `htmlFor`, `aria-describedby`, `aria-invalid` and `role="alert"` are
 * generated rather than remembered. That is the difference between a form that
 * happens to be accessible and one that cannot easily stop being.
 */

import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import clsx from 'clsx'

const CONTROL = [
  'w-full rounded-lg border bg-surface px-3 text-base text-ink',
  'placeholder:text-ink-subtle',
  'transition-colors',
  'disabled:cursor-not-allowed disabled:bg-canvas disabled:text-ink-subtle',
  'aria-[invalid=true]:border-danger aria-[invalid=true]:bg-danger-soft/40',
].join(' ')

interface FieldShellProps {
  label?: string
  description?: string
  error?: string
  required?: boolean
  hint?: ReactNode
  className?: string
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => ReactNode
}

export function Field({
  label,
  description,
  error,
  required,
  hint,
  className,
  children,
}: FieldShellProps) {
  const id = useId()
  const descriptionId = `${id}-description`
  const errorId = `${id}-error`
  const describedBy =
    [description && descriptionId, error && errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className={clsx('space-y-1.5', className)}>
      {label && (
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor={id} className="text-sm font-medium text-ink">
            {label}
            {required && (
              <span className="ml-0.5 text-danger" aria-hidden>
                *
              </span>
            )}
          </label>
          {hint && <span className="text-xs text-ink-subtle">{hint}</span>}
        </div>
      )}

      {children({ id, describedBy, invalid: Boolean(error) })}

      {description && !error && (
        <p id={descriptionId} className="text-xs text-ink-subtle">
          {description}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs font-medium text-danger-ink">
          {error}
        </p>
      )}
    </div>
  )
}

export interface TextInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'size'> {
  label?: string
  description?: string
  error?: string
  hint?: ReactNode
  containerClassName?: string
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { label, description, error, hint, required, className, containerClassName, ...rest },
  ref,
) {
  return (
    <Field
      label={label}
      description={description}
      error={error}
      required={required}
      hint={hint}
      className={containerClassName}
    >
      {({ id, describedBy, invalid }) => (
        <input
          {...rest}
          ref={ref}
          id={id}
          required={required}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className={clsx(CONTROL, 'h-9', className)}
        />
      )}
    </Field>
  )
})

export interface TextAreaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> {
  label?: string
  description?: string
  error?: string
  hint?: ReactNode
  containerClassName?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, description, error, hint, required, className, containerClassName, rows = 4, ...rest },
  ref,
) {
  return (
    <Field
      label={label}
      description={description}
      error={error}
      required={required}
      hint={hint}
      className={containerClassName}
    >
      {({ id, describedBy, invalid }) => (
        <textarea
          {...rest}
          ref={ref}
          id={id}
          rows={rows}
          required={required}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className={clsx(CONTROL, 'py-2 leading-relaxed', className)}
        />
      )}
    </Field>
  )
})

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label?: string
  description?: string
  error?: string
  hint?: ReactNode
  options: SelectOption[]
  placeholder?: string
  containerClassName?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    label,
    description,
    error,
    hint,
    options,
    placeholder,
    required,
    className,
    containerClassName,
    ...rest
  },
  ref,
) {
  return (
    <Field
      label={label}
      description={description}
      error={error}
      required={required}
      hint={hint}
      className={containerClassName}
    >
      {({ id, describedBy, invalid }) => (
        <select
          {...rest}
          ref={ref}
          id={id}
          required={required}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          className={clsx(CONTROL, 'h-9 pr-8', className)}
        >
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
})

export const Checkbox = forwardRef<
  HTMLInputElement,
  Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label: string; description?: string }
>(function Checkbox({ label, description, className, ...rest }, ref) {
  const id = useId()
  return (
    <div className="flex gap-2.5">
      <input
        {...rest}
        ref={ref}
        id={id}
        type="checkbox"
        className={clsx(
          'mt-0.5 h-4 w-4 shrink-0 rounded border-line text-brand',
          'focus-visible:ring-2',
          className,
        )}
      />
      <div className="space-y-0.5">
        <label htmlFor={id} className="cursor-pointer text-sm text-ink">
          {label}
        </label>
        {description && <p className="text-xs text-ink-subtle">{description}</p>}
      </div>
    </div>
  )
})

/** A row of controls that collapses to one column on small screens. */
export function FieldRow({
  children,
  columns = 2,
}: {
  children: ReactNode
  columns?: 2 | 3
}) {
  return (
    <div
      className={clsx(
        'grid gap-4',
        columns === 2 ? 'sm:grid-cols-2' : 'sm:grid-cols-2 lg:grid-cols-3',
      )}
    >
      {children}
    </div>
  )
}
