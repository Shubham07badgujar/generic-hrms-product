/**
 * Every customer on the deployment, and the form that creates one.
 *
 * The list is the one queryset in the product that is SUPPOSED to span
 * tenants, which is safe to say here and nowhere else: `Organization` is the
 * tenant, so scoping it by itself would be circular, and reaching this view at
 * all requires the platform flag on the user row.
 *
 * WHAT THE COLUMNS ARE, AND WHAT THEY ARE NOT. Name, state, plan and a
 * headcount. The headcount is commercial metadata — it is what a seat limit is
 * measured against, and an operator who cannot see it cannot answer "is this
 * customer about to be refused their next hire". Knowing a company employs 118
 * people says nothing about any of the 118, and there is no column here, or
 * route behind here, that would.
 *
 * PROVISIONING IS ONE CALL because it is one transaction on the server:
 * organization, settings, subscription, default configuration, first
 * administrator and the invitation, or none of it. A half-provisioned customer
 * is the thing this form must never be able to produce, so it collects the
 * fields and leaves every rule to the service — which is also why a refusal is
 * shown verbatim rather than being second-guessed here.
 *
 * THE TEMPORARY PASSWORD IS NOT SHOWN, because the API does not return it. It
 * goes to the invitation email and nowhere else; putting a live credential in
 * an HTTP response copies it into every log and proxy between here and the
 * browser. When `invitation_sent` comes back false the console says so plainly
 * — the remedy is an operator setting a password out of band, not a lookup,
 * because it is stored nowhere.
 */

import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Card, PageHeader } from '@/components/ui/Card'
import {
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { usePlatformOrganizations, usePlatformPlans, useProvisionOrganization } from './queries'
import { ORGANIZATION_STATUSES, ORGANIZATION_STATUS_LABELS, OrganizationStatusBadge } from './status'
import type { PlatformOrganization, ProvisionRequest } from './types'

function seats(row: PlatformOrganization) {
  const limit = row.subscription?.employee_limit ?? null
  if (limit === null) return `${row.employee_count}`
  return `${row.employee_count} / ${limit}`
}

function ProvisionDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const provision = useProvisionOrganization()
  const plans = usePlatformPlans()
  const toast = useToast()
  const navigate = useNavigate()
  const [form, setForm] = useState<ProvisionRequest>({ name: '', admin_email: '' })
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  function set(key: keyof ProvisionRequest, value: string) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  async function submit() {
    setError(null)
    setFieldErrors({})
    try {
      const created = await provision.mutateAsync({
        ...form,
        // Empty strings are "not supplied" to the serializer's optional
        // fields; sending them is how a blank slug becomes a validation error
        // instead of letting the service derive one from the name.
        slug: form.slug || undefined,
        legal_name: form.legal_name || undefined,
        admin_first_name: form.admin_first_name || undefined,
        admin_last_name: form.admin_last_name || undefined,
        plan: form.plan || undefined,
      })
      toast.push({
        tone: created.invitation_sent ? 'success' : 'warning',
        title: `${created.name} created`,
        description: created.invitation_sent
          ? `An invitation was sent to ${created.admin_email}.`
          : `The invitation to ${created.admin_email} could not be sent. Their temporary ` +
            'password is stored nowhere — set one out of band.',
      })
      setForm({ name: '', admin_email: '' })
      onClose()
      navigate(`/platform/organizations/${created.id}`)
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.displayMessage)
        setFieldErrors(caught.fieldErrors)
      } else {
        setError('Could not create the organization.')
      }
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={provision.isPending}
      title="New organization"
      description="Creates the organization, its default configuration and its first administrator in one transaction."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={provision.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            loading={provision.isPending}
            disabled={!form.name || !form.admin_email}
          >
            Create organization
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Banner tone="danger" title="Not created">
            {error}
          </Banner>
        )}
        <TextInput
          label="Organization name"
          required
          value={form.name}
          error={fieldErrors.name}
          onChange={(event) => set('name', event.target.value)}
        />
        <TextInput
          label="Legal name"
          value={form.legal_name ?? ''}
          error={fieldErrors.legal_name}
          onChange={(event) => set('legal_name', event.target.value)}
        />
        <TextInput
          label="Slug"
          hint="Left blank, the service derives one from the name and refuses if it cannot."
          value={form.slug ?? ''}
          error={fieldErrors.slug}
          onChange={(event) => set('slug', event.target.value)}
        />
        <TextInput
          label="Administrator email"
          type="email"
          required
          hint="The invitation goes here. This address becomes their sign-in."
          value={form.admin_email}
          error={fieldErrors.admin_email}
          onChange={(event) => set('admin_email', event.target.value)}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput
            label="Administrator first name"
            value={form.admin_first_name ?? ''}
            onChange={(event) => set('admin_first_name', event.target.value)}
          />
          <TextInput
            label="Administrator last name"
            value={form.admin_last_name ?? ''}
            onChange={(event) => set('admin_last_name', event.target.value)}
          />
        </div>
        <Select
          label="Plan"
          value={form.plan ?? ''}
          error={fieldErrors.plan}
          onChange={(event) => set('plan', event.target.value)}
          options={[
            { value: '', label: 'Deployment default' },
            ...(plans.data?.data ?? []).map((plan) => ({
              value: plan.code,
              label: plan.name,
            })),
          ]}
        />
      </div>
    </Modal>
  )
}

export function PlatformOrganizationsPage() {
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const navigate = useNavigate()

  const status = params.get('status') ?? ''
  const query = usePlatformOrganizations({ search, status })

  const columns: Array<Column<PlatformOrganization>> = [
    {
      key: 'name',
      header: 'Organization',
      render: (row) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium text-ink">{row.name}</span>
          <span className="block truncate text-xs text-ink-subtle">{row.slug}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => <OrganizationStatusBadge status={row.status} />,
    },
    {
      key: 'plan',
      header: 'Plan',
      secondary: true,
      render: (row) => row.subscription?.plan_name ?? '—',
    },
    {
      key: 'seats',
      header: 'Employees',
      align: 'right',
      render: (row) => <span className="tabular-nums">{seats(row)}</span>,
    },
    {
      key: 'created',
      header: 'Created',
      secondary: true,
      align: 'right',
      render: (row) => formatDate(row.created_at),
    },
  ]

  return (
    <>
      <PageHeader
        title="Organizations"
        description="Every customer on this deployment."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New organization
          </Button>
        }
      />

      <TableToolbar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search by name, slug or email…"
        hasFilters={Boolean(status || search)}
        onReset={() => {
          setSearch('')
          setParams({})
        }}
      >
        <FilterSelect
          label="Status"
          value={status}
          onChange={(value) => setParams(value ? { status: value } : {})}
          allLabel="Any status"
          options={ORGANIZATION_STATUSES.map((code) => ({
            value: code,
            label: ORGANIZATION_STATUS_LABELS[code],
          }))}
        />
      </TableToolbar>

      {query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={query.data?.data ?? []}
            rowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/platform/organizations/${row.id}`)}
            isLoading={query.isLoading}
            loadingState={<TableSkeleton columns={5} />}
            emptyState={
              <EmptyState
                title="No organizations"
                description="Nothing matches these filters. A deployment with no customers is also a valid answer."
              />
            }
          />
        </Card>
      )}

      <ProvisionDialog open={creating} onClose={() => setCreating(false)} />
    </>
  )
}
