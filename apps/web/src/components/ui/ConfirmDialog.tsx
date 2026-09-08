/**
 * Confirmation dialogs.
 *
 * `ConfirmDialog` covers ordinary destructive actions. `ReasonDialog` covers
 * the ones the backend refuses without a written justification — HR rejection
 * and the Admin override — and enforces the same 20-character floor the API
 * and a database CheckConstraint enforce.
 *
 * Three layers agreeing is the point. The client copy exists so the user finds
 * out before submitting, not so the rule is weaker anywhere else.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { Button } from './Button'
import { Modal } from './Modal'
import { TextArea } from './Field'

export const MIN_REASON_LENGTH = 20

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'primary',
  loading,
  children,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  loading?: boolean
  children?: ReactNode
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      busy={loading}
      size="sm"
      footer={
        <>
          <Button onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button variant={tone === 'danger' ? 'danger' : 'primary'} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  )
}

export function ReasonDialog({
  open,
  onClose,
  onSubmit,
  title,
  description,
  label = 'Reason',
  hint,
  confirmLabel = 'Confirm',
  tone = 'danger',
  loading,
  serverError,
  banner,
  children,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (reason: string) => void
  title: string
  description?: ReactNode
  label?: string
  hint?: ReactNode
  confirmLabel?: string
  tone?: 'primary' | 'danger'
  loading?: boolean
  serverError?: string
  banner?: ReactNode
  children?: ReactNode
}) {
  const [reason, setReason] = useState('')
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (open) {
      setReason('')
      setTouched(false)
    }
  }, [open])

  const trimmed = reason.trim()
  const tooShort = trimmed.length < MIN_REASON_LENGTH
  const clientError = touched && tooShort ? `At least ${MIN_REASON_LENGTH} characters.` : undefined

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      busy={loading}
      footer={
        <>
          <Button onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            loading={loading}
            // Disabled rather than hidden: the user can see the action exists
            // and the counter tells them exactly what unlocks it.
            disabled={tooShort}
            onClick={() => onSubmit(trimmed)}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {banner}
        {children}
        <TextArea
          label={label}
          required
          rows={5}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          onBlur={() => setTouched(true)}
          error={clientError ?? serverError}
          description={
            typeof hint === 'string' || hint === undefined
              ? (hint as string) ??
                'This is recorded permanently against the candidate and is visible in their history.'
              : undefined
          }
          hint={
            <span className={tooShort ? 'text-ink-subtle' : 'text-success-ink'}>
              {trimmed.length}/{MIN_REASON_LENGTH}
            </span>
          }
        />
        {hint && typeof hint !== 'string' && <div>{hint}</div>}
      </div>
    </Modal>
  )
}
