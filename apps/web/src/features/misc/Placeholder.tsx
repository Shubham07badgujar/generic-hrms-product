/**
 * Honest placeholders.
 *
 * The sidebar shows a module when the user's ROLE grants it, and several
 * modules (attendance, leave, payroll, reporting) have permissions seeded but
 * no API yet. Rather than hide the entries — which would misrepresent what the
 * role covers — or 404 on a link the app itself rendered, these pages say what
 * is coming and which phase builds it.
 */

import { Link } from 'react-router-dom'
import { Card, PageHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/States'
import { Button } from '@/components/ui/Button'

export function PlaceholderPage({ title, phase }: { title: string; phase: string }) {
  return (
    <>
      <PageHeader title={title} />
      <Card>
        <EmptyState
          title={`${title} is built in a later phase`}
          description={
            `Your role includes this module, and the permissions are already in place — but the ` +
            `${phase} API has not been built yet. This page will fill in when it lands.`
          }
          icon={
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden>
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
              <path d="M12 7v5.2l3.2 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          }
          action={
            <Link to="/">
              <Button>Back to dashboard</Button>
            </Link>
          }
        />
      </Card>
    </>
  )
}

export function NotFoundPage() {
  return (
    <Card>
      <EmptyState
        title="Page not found"
        description="That address does not match anything in the application."
        action={
          <Link to="/">
            <Button variant="primary">Back to dashboard</Button>
          </Link>
        }
      />
    </Card>
  )
}
