/**
 * The operator's entrance.
 *
 * ITS OWN PAGE, not a third variant of `LoginPage`, for one reason that is
 * visible the moment you look at the other file: that page reads
 * `useBranding()` and paints a customer's logo, legal name and HR marketing
 * copy across half the screen. Branding resolves from the host or the sole
 * organization on the deployment — meaning the SaaS operator's sign-in screen
 * would carry whichever customer the request happened to resolve to. That is
 * the wrong product wearing the wrong company's name, and on a multi-customer
 * deployment it is a cross-tenant identity leak on a public page.
 *
 * So this screen is deliberately unbranded: no logo, no organization name,
 * nothing fetched before authentication.
 *
 * NOT LINKED from the customer sign-in page either, unlike the administrator
 * entrance. Nothing is hidden by that — the backend's throttles and its
 * deliberately generic failure message are what protect the door — but an
 * operator console advertised on every customer's login screen is an
 * invitation to probe it, and operators know their own URL.
 *
 * The failure message is shown verbatim for the same reason it is on the other
 * page: the API answers identically for a wrong password and for a valid
 * non-operator using this entrance, so the screen never reveals which account
 * is which.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useAuth } from '@/app/AuthProvider'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { ApiError } from '@/lib/api'

const schema = z.object({
  email: z.string().min(1, 'Enter your email address.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.'),
})

type FormValues = z.infer<typeof schema>

export function PlatformLoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    setFormError(null)
    try {
      await login(values.email, values.password, 'platform')
      navigate('/platform', { replace: true })
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? error.displayMessage
          : 'Could not sign in. Please try again.',
      )
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-7 space-y-1 text-center">
          <p className="text-2xs font-semibold uppercase tracking-[0.22em] text-ink-subtle">
            Platform console
          </p>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Operator sign-in</h1>
          <p className="text-sm text-ink-muted">
            For the team that runs this deployment. Customer sign-in is elsewhere.
          </p>
        </div>

        <form
          onSubmit={handleSubmit(onSubmit)}
          noValidate
          className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-overlay sm:p-7"
        >
          {formError && (
            <Banner tone="danger" title="Sign-in failed">
              {formError}
            </Banner>
          )}

          <TextInput
            label="Email address"
            type="email"
            autoComplete="username"
            autoFocus
            required
            error={errors.email?.message}
            {...register('email')}
          />

          <TextInput
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            error={errors.password?.message}
            {...register('password')}
          />

          <Button type="submit" variant="primary" size="lg" fullWidth loading={isSubmitting}>
            Sign in
          </Button>

          <p className="pt-1 text-center text-xs text-ink-subtle">
            This entrance is monitored and rate-limited. Operator accounts are created with a
            management command, never through this application.
          </p>
        </form>
      </div>
    </div>
  )
}
