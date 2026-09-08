/**
 * The employee handbook, shown right after the first successful password
 * reset and until it is acknowledged.
 *
 * Bare — no AppShell — because while the acknowledgement is owed this is the
 * only destination the guard allows, and it should read as a document, not an
 * app. Acknowledging completes the employee's own checklist line through the
 * ordinary onboarding-items endpoint (their EDIT is scoped to SELF), so HR
 * sees the same line every other checklist item uses.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/Button'
import { Banner } from '@/components/ui/Misc'
import { Spinner } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/Toast'
import { useAuth } from '@/app/AuthProvider'
import { apiGet, apiPost, type Paginated } from '@/lib/api'
import type { OnboardingItem } from '@/lib/types'
import { useBranding } from '@/lib/branding'

/*
 * STARTER CONTENT. The employer's name below is live branding, but the POLICY
 * TEXT is a neutral template every organisation is expected to replace with
 * its own handbook — a clinic will want patient-confidentiality wording, a
 * factory will want safety rules, and neither belongs in a shared product.
 * Replace the sections in this file, or hide the route. See
 * docs/CONFIGURATION.md.
 */

function usePendingAcknowledgement() {
  return useQuery({
    queryKey: ['handbook-acknowledgement'],
    queryFn: () =>
      apiGet<Paginated<OnboardingItem>>('/onboarding-items/', {
        params: { kind: 'acknowledgement', status: 'pending' },
      }).then((page) => page.data[0] ?? null),
  })
}

export function HandbookPage() {
  const branding = useBranding()
  const { user, reloadUser } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const pending = usePendingAcknowledgement()

  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)

  const mustAcknowledge = Boolean(user?.handbook_acknowledgement_pending)

  async function acknowledge() {
    if (!pending.data || busy) return
    setBusy(true)
    try {
      await apiPost(`/onboarding-items/${pending.data.id}/complete/`, {
        notes: 'Acknowledged in-app by the employee.',
      })
      await reloadUser()
      toast.success('Handbook acknowledged', 'Welcome aboard!')
      navigate('/', { replace: true })
    } catch {
      toast.error('Could not record the acknowledgement', 'Try again in a moment.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-canvas px-4 py-10">
      <div className="mx-auto max-w-2xl space-y-6">
        <header className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">{branding.name}</p>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Employee Handbook &amp; Company Policies
          </h1>
          <p className="text-sm text-ink-muted">
            {mustAcknowledge
              ? 'Please read this through — your acknowledgement at the end is required before you continue to the HRMS.'
              : 'For your reference. You have already acknowledged this handbook.'}
          </p>
        </header>

        <div className="space-y-5 rounded-2xl border border-line bg-surface p-6 text-sm leading-6 text-ink shadow-card">
          <Section title="Welcome">
            <p>
              Welcome to {branding.name}. This handbook summarises the policies that apply to your
              employment. The HR team is your first point of contact for anything it leaves
              unanswered.
            </p>
          </Section>

          <Section title="Conduct and confidentiality">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                Treat colleagues, customers and visitors with courtesy and respect at all times.
              </li>
              <li>
                Confidential information — about the organisation, its customers or your
                colleagues — stays inside the team that needs it. Never share it outside
                that team, in person or online.
              </li>
              <li>
                Company information — schedules, records, financials, credentials — stays within
                the company and is used only for your work.
              </li>
            </ul>
          </Section>

          <Section title="Attendance and absence">
            <ul className="list-disc space-y-1 pl-5">
              <li>Report planned absences in advance through the HRMS leave module.</li>
              <li>
                An unreported absence of 3 or more consecutive working days is flagged to HR and
                treated as a serious matter.
              </li>
            </ul>
          </Section>

          <Section title="Leave policy">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                <strong>19 paid leave days per year</strong> (privilege and casual leave combined),
                accruing at roughly 1.58 days per month of service. Leave can be taken only once it
                has accrued — there is no advance leave.
              </li>
              <li>
                During <strong>probation</strong>, leave is unpaid and does not draw on the paid
                balance.
              </li>
              <li>A single stretch of leave may not exceed 6 consecutive days.</li>
              <li>
                <strong>Notice:</strong> short leaves need at least 72 hours' notice; longer
                spans need 7 days; extended leave needs a month. Genuine emergencies may be filed
                within 24 hours with the emergency flag.
              </li>
              <li>Sick leave requires a medical certificate attached to the request.</li>
              <li>
                Leaving 3 or more hours early counts as a <strong>short leave</strong>; short
                leaves are converted monthly, first against the paid balance, then as loss of pay.
              </li>
              <li>
                Working on a public holiday, when approved, is compensated at{' '}
                <strong>double pay</strong>.
              </li>
            </ul>
          </Section>

          <Section title="Public holidays (2026)">
            <ul className="list-disc space-y-1 pl-5">
              <li>Holi — 4 March</li>
              <li>Ganesh Chaturthi — 14 September &amp; 25 September</li>
              <li>Diwali — 8 November &amp; 11 November</li>
            </ul>
          </Section>

          <Section title="IT and account security">
            <ul className="list-disc space-y-1 pl-5">
              <li>
                Your HRMS login is personal. Keep your password confidential and never share it —
                with anyone, including HR.
              </li>
              <li>Lock your screen when away and report anything suspicious to HR at once.</li>
            </ul>
          </Section>
        </div>

        {mustAcknowledge ? (
          pending.isLoading ? (
            <div className="flex justify-center py-4">
              <Spinner className="h-5 w-5" label="Loading your acknowledgement" />
            </div>
          ) : pending.data ? (
            <div className="space-y-4 rounded-2xl border border-line bg-surface p-6 shadow-card">
              <label className="flex cursor-pointer items-start gap-3 text-sm text-ink">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={agreed}
                  onChange={(event) => setAgreed(event.target.checked)}
                />
                <span>
                  I confirm that I have read and understood the Employee Handbook and company
                  policies of {branding.name}, and I agree to abide by them.
                </span>
              </label>
              <Button
                variant="primary"
                size="lg"
                fullWidth
                disabled={!agreed || busy}
                loading={busy}
                onClick={acknowledge}
              >
                Acknowledge and continue
              </Button>
            </div>
          ) : (
            <Banner tone="warning" title="Could not find your acknowledgement item">
              Your checklist has no open handbook acknowledgement. Continue to the HRMS — if this
              looks wrong, contact HR.
              <div className="pt-3">
                <Button onClick={() => navigate('/', { replace: true })}>Continue</Button>
              </div>
            </Banner>
          )
        ) : (
          <Button fullWidth onClick={() => navigate('/', { replace: true })}>
            Back to the HRMS
          </Button>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {children}
    </section>
  )
}
