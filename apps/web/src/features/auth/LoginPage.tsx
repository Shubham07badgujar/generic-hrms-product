import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useAuth } from '@/app/AuthProvider'
import { Button } from '@/components/ui/Button'
import { TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { ApiError } from '@/lib/api'
import { useBranding } from '@/lib/branding'

const schema = z.object({
  email: z.string().min(1, 'Enter your email address.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.'),
})

type FormValues = z.infer<typeof schema>

export function LoginPage({ adminEntrance = false }: { adminEntrance?: boolean }) {
  const branding = useBranding()
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const redirectTo = (location.state as { from?: string } | null)?.from ?? '/'

  async function onSubmit(values: FormValues) {
    setFormError(null)
    try {
      await login(values.email, values.password, adminEntrance)
      navigate(redirectTo, { replace: true })
    } catch (error) {
      // The API returns a deliberately generic message for bad credentials —
      // including for a valid non-admin using the admin entrance, so this page
      // never reveals whether an address belongs to an administrator. It is
      // shown verbatim rather than being "improved" here.
      setFormError(
        error instanceof ApiError
          ? error.displayMessage
          : 'Could not sign in. Please try again.',
      )
    }
  }

  // The logo's lime — the same explicit accent the hero, sidebar and offer
  // letterhead use, chosen to read identically on light and dark surfaces.
  const LIME = '#A6CE39'

  return (
    <div className="flex min-h-screen bg-canvas">
      {/* The brand panel: committed colours (white + lime on brand blue), so
          it is one deliberate visual world in either theme. */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-gradient-to-br from-brand to-brand-hover p-10 text-white lg:flex xl:p-14">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-28 h-96 w-96 rounded-full border-[14px] motion-safe:animate-float-slow"
          style={{ borderColor: `${LIME}55` }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-32 -left-20 h-80 w-80 rounded-full motion-safe:animate-float-slow"
          style={{ backgroundColor: `${LIME}14`, animationDelay: '2.5s' }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute right-1/4 top-1/2 h-24 w-24 rounded-full border-[7px] motion-safe:animate-float-slow"
          style={{ borderColor: `${LIME}2e`, animationDelay: '4.5s' }}
        />
        {/* The organisation introduces itself: name and logo come from
            Organisation -> Settings via /org/branding/ — nothing is compiled in. */}
        <div className="relative flex items-center gap-3 motion-safe:animate-slide-up">
          {branding.logo ? (
            <img
              src={branding.logo}
              alt=""
              className="h-11 w-11 shrink-0 rounded-xl bg-white/90 object-contain p-1"
            />
          ) : (
            <span
              aria-hidden
              className="inline-block h-[19px] w-[19px] shrink-0 rounded-full border-[5px]"
              style={{ borderColor: LIME }}
            />
          )}
          <span>
            <p className="text-[26px] font-extrabold uppercase leading-tight tracking-tight">
              {branding.name}
            </p>
            {branding.legal_name && branding.legal_name !== branding.name && (
              <p className="mt-0.5 text-2xs font-medium uppercase tracking-[0.28em] text-white/70">
                {branding.legal_name}
              </p>
            )}
          </span>
        </div>

        <div className="relative max-w-md space-y-6">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight xl:text-4xl">
            One place for our
            <span className="block" style={{ color: LIME }}>
              people and their work.
            </span>
          </h2>
          <div className="grid grid-cols-2 gap-2.5 text-sm">
            {[
              ['🧭', 'Recruitment'],
              ['🌱', 'Onboarding'],
              ['🕘', 'Attendance'],
              ['🌴', 'Leave'],
              ['💰', 'Payroll'],
              ['🔍', 'Audit trail'],
            ].map(([icon, label]) => (
              <span
                key={label}
                className="flex items-center gap-2.5 rounded-xl bg-white/10 px-3 py-2 backdrop-blur-sm"
              >
                <span aria-hidden>{icon}</span>
                <span className="font-medium text-white/90">{label}</span>
              </span>
            ))}
          </div>
          {/* Product-true copy only. Anything specific to one organisation —
              its branches, its headcount — would be a false claim on the next
              deployment, and this page is public. */}
          <p className="text-sm leading-relaxed text-white/70">
            Every location on one platform, with the same rules everywhere.
          </p>
        </div>

        <p className="relative text-2xs text-white/60">
          Access is issued by HR and every action is recorded in the audit trail.
        </p>
      </div>

      {/* The form side rides the theme tokens: white in light, slate in dark. */}
      <div className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm motion-safe:animate-slide-up">
          <div className="mb-7 flex flex-col items-center gap-3 text-center">
            <span className="relative flex h-11 w-11 items-center justify-center rounded-xl bg-brand lg:hidden">
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
                {adminEntrance ? 'Restricted entrance' : 'Welcome back'}
              </p>
              <h1 className="text-2xl font-semibold tracking-tight text-ink">
                {adminEntrance ? 'Administrator sign-in' : 'Sign in to HRMS'}
              </h1>
              <p className="text-sm text-ink-muted">
                {adminEntrance
                  ? 'This entrance is monitored and rate-limited.'
                  : 'Use the credentials issued by your HR team.'}
              </p>
            </div>
          </div>

          <form
            onSubmit={handleSubmit(onSubmit)}
            noValidate
            className="relative space-y-4 overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-overlay sm:p-7"
          >
            <div
              aria-hidden
              className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r to-transparent"
              style={{ backgroundImage: `linear-gradient(to right, ${LIME}, ${LIME}66, transparent)` }}
            />
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
              Accounts are created by HR. There is no public sign-up.
            </p>
          </form>

          <p className="mt-5 text-center text-xs text-ink-subtle">
            {adminEntrance ? (
              <a href="/login" className="text-brand hover:underline">
                Back to the standard sign-in
              </a>
            ) : (
              <a href="/login/admin" className="text-ink-subtle hover:text-ink hover:underline">
                Administrator sign-in
              </a>
            )}
          </p>
        </div>
      </div>
    </div>
  )
}
