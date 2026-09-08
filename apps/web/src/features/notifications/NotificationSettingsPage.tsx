/**
 * Notification preferences.
 *
 * The server returns every kind, including ones never configured, so this page
 * shows a complete list without holding a copy of the enum. `configured: false`
 * means the row is showing the default rather than a choice the user made —
 * worth saying, because "on" and "never touched" look identical otherwise.
 */

import { Card, PageHeader, Section } from '@/components/ui/Card'
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/States'
import { Checkbox } from '@/components/ui/Field'
import { Banner, Muted } from '@/components/ui/Misc'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useNotificationPreferences,
  useSetNotificationPreference,
} from '@/lib/analyticsQueries'
import type { NotificationPreferenceRow } from '@/lib/types'

/** Grouped by the workflow they belong to, from the kind's own prefix. */
const GROUPS: Array<{ title: string; match: (kind: string) => boolean }> = [
  { title: 'Recruitment', match: (k) => k.startsWith('interview') || k.startsWith('candidate') || k.startsWith('offer') || k.startsWith('department_decision') },
  { title: 'Employment', match: (k) => k.startsWith('onboarding') || k.startsWith('probation') || k.startsWith('document') },
  { title: 'Offboarding', match: (k) => k.startsWith('resignation') || k.startsWith('clearance') || k.startsWith('exit') },
  { title: 'Payroll', match: (k) => k.startsWith('payroll') || k.startsWith('payslip') || k.startsWith('statutory') },
  { title: 'Assets and accounts', match: (k) => k.startsWith('asset') || k.startsWith('email_account') },
  { title: 'Administration', match: (k) => k.startsWith('role') || k.startsWith('admin') },
]

function PreferenceRow({ row }: { row: NotificationPreferenceRow }) {
  const save = useSetNotificationPreference()
  const toast = useToast()
  const permissions = usePermissions()
  const canEdit = permissions.can(RESOURCE.NOTIFICATION, ACTION.EDIT)

  function update(patch: Partial<Pick<NotificationPreferenceRow, 'in_app' | 'email'>>) {
    save.mutate(
      { kind: row.kind, in_app: row.in_app, email: row.email, ...patch },
      {
        onError: () => toast.push({ tone: 'error', title: 'Could not save that preference' }),
      },
    )
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-2.5 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink">{row.label}</p>
        {!row.configured ? <Muted className="text-xs">Using the default</Muted> : null}
      </div>
      <div className="flex items-center gap-4">
        <Checkbox
          label="In app"
          checked={row.in_app}
          disabled={!canEdit || save.isPending}
          onChange={(event) => update({ in_app: event.target.checked })}
        />
        <Checkbox
          label="Email"
          checked={row.email}
          disabled={!canEdit || save.isPending}
          onChange={(event) => update({ email: event.target.checked })}
        />
      </div>
    </div>
  )
}

export function NotificationSettingsPage() {
  const permissions = usePermissions()
  const canView = permissions.can(RESOURCE.NOTIFICATION, ACTION.VIEW)
  const canEdit = permissions.can(RESOURCE.NOTIFICATION, ACTION.EDIT)
  const query = useNotificationPreferences(canView)

  if (!canView) {
    return (
      <>
        <PageHeader title="Notification settings" />
        <EmptyState title="Notifications are not available to your account" />
      </>
    )
  }

  if (query.isLoading) return <CardSkeleton />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const rows = query.data?.preferences ?? []
  const ungrouped = rows.filter((row) => !GROUPS.some((g) => g.match(row.kind)))

  return (
    <>
      <PageHeader
        title="Notification settings"
        description="Choose what you are told about, and how."
      />

      {!canEdit ? (
        <Banner tone="info" title="Read-only">
          Your account can read notifications but not change these preferences.
        </Banner>
      ) : null}

      <Banner tone="neutral">
        Notifications marked <strong>action required</strong> are always delivered in the
        app. They mean something is blocked on you — a payroll run, an exit, a probation
        decision — and switching those off would turn a preference into a way of missing
        an obligation.
      </Banner>

      {GROUPS.map((group) => {
        const groupRows = rows.filter((row) => group.match(row.kind))
        if (groupRows.length === 0) return null
        return (
          <Section key={group.title} title={group.title}>
            <Card>
              {groupRows.map((row) => (
                <PreferenceRow key={row.kind} row={row} />
              ))}
            </Card>
          </Section>
        )
      })}

      {ungrouped.length > 0 ? (
        <Section title="Other">
          <Card>
            {ungrouped.map((row) => (
              <PreferenceRow key={row.kind} row={row} />
            ))}
          </Card>
        </Section>
      ) : null}
    </>
  )
}
