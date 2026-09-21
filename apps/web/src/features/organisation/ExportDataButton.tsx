/**
 * Download the organization's whole record.
 *
 * Rendered only when `/me/` says `organization_export_available`, which the
 * server computes with the SAME function the export route uses -- so this
 * button never appears for someone the route would refuse, and never hides
 * from someone it would serve. It is not a permission check and does not
 * pretend to be one: the route decides, and a refusal it gives is shown
 * verbatim.
 *
 * A download through the authenticated client rather than a link: the access
 * token lives in memory, so a plain `<a href>` would arrive with no credentials
 * and be refused.
 */

import { useState } from 'react'
import { useAuth } from '@/app/AuthProvider'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { ApiError, downloadFile } from '@/lib/api'

export function ExportDataButton({ variant = 'primary' }: { variant?: 'primary' | 'secondary' }) {
  const { user } = useAuth()
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  if (!user?.organization_export_available) return null

  async function download() {
    setBusy(true)
    try {
      await downloadFile('/org/export/', 'organization-export.zip')
    } catch (error) {
      toast.error(
        'Export failed',
        error instanceof ApiError ? error.displayMessage : 'The export could not be downloaded.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button variant={variant} loading={busy} onClick={() => void download()}>
      Download your data
    </Button>
  )
}
