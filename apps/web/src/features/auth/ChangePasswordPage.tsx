/**
 * Choose a new password.
 *
 * Reached two ways: forced, on the first sign-in with a temporary password
 * (the RequireAuth guard sends anyone flagged `must_change_password` here and
 * nowhere else); and voluntarily, from the account menu, any time after.
 *
 * The API refuses every other request while the flag is up, so this page is
 * the only thing a temporary password can reach — the guard is a courtesy
 * that keeps the user from seeing doors that will not open, and the server is
 * the lock. On success the API revokes every session including this one, so
 * the page signs the user out and sends them to log in with what they chose.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { useToast } from '@/components/ui/Toast'
import { useAuth } from '@/app/AuthProvider'
import { ApiError, apiPost } from '@/lib/api'

const MIN_LENGTH = 12

export function ChangePasswordPage() {
  const { user, mustChangePassword, logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const mismatch = confirm !== '' && next !== confirm
  const tooShort = next !== '' && next.length < MIN_LENGTH
  const ready = current && next.length >= MIN_LENGTH && next === confirm && !busy

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    setFieldErrors({})
    try {
      await apiPost('/auth/change-password/', { current_password: current, new_password: next })
      toast.success('Password changed', 'Sign in again with your new password.')
      // The server revoked every session, this one included. Ending it
      // locally and returning to sign-in is the honest next step.
      await logout()
      navigate('/login', { replace: true })
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setFieldErrors(submitError.fieldErrors)
        setError(submitError.displayMessage)
      } else {
        setError('Could not change the password. Try again.')
      }
    } finally {
      setBusy(false)
    }
  }

  // The logo's lime — the same explicit accent as the login page, sidebar
  // and hero; it reads identically on the light and the dark canvas.
  const LIME = '#A6CE39'

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas p-4">
      {/* the enso rings, drifting quietly behind the card */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full border-[12px] motion-safe:animate-float-slow"
        style={{ borderColor: `${LIME}2e` }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-28 -left-24 h-72 w-72 rounded-full motion-safe:animate-float-slow"
        style={{ backgroundColor: `${LIME}12`, animationDelay: '3s' }}
      />

      <div className="relative w-full max-w-md motion-safe:animate-slide-up">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand">
            <span
              aria-hidden
              className="block h-5 w-5 rounded-full border-4"
              style={{ borderColor: LIME }}
            />
          </span>
          <div className="space-y-1">
            <p
              className="text-2xs font-semibold uppercase tracking-[0.22em]"
              style={{ color: LIME }}
            >
              {mustChangePassword ? 'One last step' : 'Account security'}
            </p>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              {mustChangePassword ? 'Choose your password' : 'Change your password'}
            </h1>
            <p className="text-sm text-ink-muted">
              {mustChangePassword
                ? 'Your account was created with a temporary password. Choose your own before continuing — the temporary one stops working the moment you do.'
                : `Signed in as ${user?.email ?? ''}.`}
            </p>
          </div>
        </div>

        <form
          onSubmit={submit}
          noValidate
          className="relative space-y-4 overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-overlay sm:p-7"
        >
          <div
            aria-hidden
            className="absolute inset-x-0 top-0 h-1"
            style={{ backgroundImage: `linear-gradient(to right, ${LIME}, ${LIME}66, transparent)` }}
          />
          {error && (
            <Banner tone="danger" title="Could not change the password">
              {error}
            </Banner>
          )}

          <TextInput
            label={mustChangePassword ? 'Temporary password' : 'Current password'}
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            error={fieldErrors.current_password}
          />
          <TextInput
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            value={next}
            onChange={(event) => setNext(event.target.value)}
            error={
              fieldErrors.new_password ??
              (tooShort ? `At least ${MIN_LENGTH} characters.` : undefined)
            }
            description={`At least ${MIN_LENGTH} characters, not a common password, and not similar to your name or email.`}
          />
          <TextInput
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            error={mismatch ? 'The two passwords do not match.' : undefined}
          />

          <Button type="submit" variant="primary" size="lg" fullWidth loading={busy} disabled={!ready}>
            {mustChangePassword ? 'Set password and continue' : 'Change password'}
          </Button>

          {!mustChangePassword && (
            <Button type="button" variant="ghost" fullWidth onClick={() => navigate(-1)}>
              Cancel
            </Button>
          )}
        </form>
      </div>
    </div>
  )
}
