/**
 * What a stopped organization sees instead of the application.
 *
 * The API refuses a suspended, cancelled or archived organization on every
 * route but three (`/auth/`, `/me/`, `/org/branding/`) and says WHY, with its
 * own code. This is the screen that code exists for: without it a stopped
 * customer and a user who simply lacks a permission produce the same refusal,
 * and the person reading it cannot tell whether to call their administrator or
 * their account manager.
 *
 * DELIBERATELY WITHOUT A RETRY. Nothing the user does here changes the
 * organization's status, and a "try again" button on a screen whose cause is
 * commercial teaches people to hammer a door that opens on somebody else's
 * decision. Signing out is offered because it is the one thing that IS theirs
 * -- and because a shared machine should not be left holding the session.
 *
 * NO DATA IS SHOWN, and that is the product decision rather than a limitation.
 * A suspended organization's data is preserved, not published: the customer
 * gets it back on restore, or through an export, both of which are acts
 * somebody performs rather than a side effect of the account still resolving.
 *
 * The export is the one act offered here, and only when the server says this
 * person may take it -- in practice a cancelled organization's Admin, inside
 * the export window. Suspended organizations are not offered it: restoring the
 * account is their remedy, and the route would refuse.
 */

import { useAuth, usePermissions } from '@/app/AuthProvider'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/States'
import { ExportDataButton } from './ExportDataButton'

const EXPLANATION: Record<string, { title: string; description: string }> = {
  suspended: {
    title: 'This account is suspended',
    description:
      'Your organization’s access has been paused. Nothing has been deleted, and ' +
      'everything returns as it was once the account is restored. Your administrator ' +
      'or account manager can tell you why and what happens next.',
  },
  cancelled: {
    title: 'This account has been cancelled',
    description:
      'Your organization’s subscription has ended. The records are retained for a ' +
      'period during which an administrator can still export them; after that they are ' +
      'removed. Contact your account manager if this is unexpected.',
  },
  archived: {
    title: 'This account has been archived',
    description:
      'Your organization is no longer active on this platform. An operator can say ' +
      'what remains available and for how long.',
  },
}

const FALLBACK = {
  title: 'This organization is not currently active',
  description:
    'The application is unavailable while the account is in this state. Your ' +
    'administrator can tell you more.',
}

export function SuspendedPage() {
  const { logout } = useAuth()
  const status = usePermissions().organizationStatus
  const copy = EXPLANATION[status] ?? FALLBACK

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-6">
      <Card className="w-full max-w-xl">
        <EmptyState
          title={copy.title}
          description={copy.description}
          icon={
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden>
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
              <path
                d="M12 7.5v5"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
              <circle cx="12" cy="16" r="0.9" fill="currentColor" />
            </svg>
          }
          action={
            <div className="flex flex-wrap items-center justify-center gap-2">
              <ExportDataButton />
              <Button variant="secondary" onClick={() => void logout()}>
                Sign out
              </Button>
            </div>
          }
        />
      </Card>
    </div>
  )
}
