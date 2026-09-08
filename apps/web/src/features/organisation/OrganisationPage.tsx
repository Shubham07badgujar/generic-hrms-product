/**
 * Organisation — the Admin Panel's management surface.
 *
 * Departments, designations, locations, seniority levels and the role
 * catalogue, each with create/edit/deactivate for whoever the matrix grants
 * it to (Admin, under the seeded matrix). Everyone else still sees the same
 * read-only reference lists this page has always served.
 *
 * The permission checks here decide which CONTROLS render; the server refuses
 * on its own authority. Two server rules are worth knowing when a change is
 * refused: nothing is ever hard-deleted, and nothing still in use — a
 * department with employees, a role people hold — can be retired.
 */

import { useMemo, useState } from 'react'
import { Card, CardHeader, PageHeader, Section } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge } from '@/components/ui/Badge'
import { Banner, Tabs, TabPanel } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Checkbox, Select, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import {
  useDepartments,
  useDesignations,
  useEmployeeLevels,
  useLocations,
  useRoles,
  useOrgSettings,
  useUpdateOrgSettings,
} from '@/lib/queries'
import {
  useAccessCatalog,
  useCatalogueMutations,
  useRoleAdmin,
  useRolePermissions,
  type CatalogueKind,
  type PermissionCell,
} from '@/lib/orgAdminQueries'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { humanize } from '@/lib/format'
import type { Department, Designation, EmployeeLevel, Location, Role, UUID } from '@/lib/types'

const DEPARTMENT_KINDS = ['medical', 'operations', 'hr', 'finance']
const LAYERS = [
  { value: '1', label: '1 — Executive' },
  { value: '2', label: '2 — Department head' },
  { value: '3', label: '3 — Manager' },
  { value: '4', label: '4 — Executive staff' },
  { value: '5', label: '5 — Staff' },
]
const SCOPE_LABELS = ['—', 'Self', 'Team', 'Department', 'All']

/* ============================================== generic catalogue editing */

interface FieldSpec {
  key: string
  label: string
  kind: 'text' | 'select' | 'number' | 'checkbox'
  options?: Array<{ value: string; label: string }>
  required?: boolean
  /** Not editable after creation (unique codes). */
  createOnly?: boolean
}

function CatalogueDialog({
  open,
  onClose,
  title,
  fields,
  initial,
  busy,
  onSubmit,
  error,
}: {
  open: boolean
  onClose: () => void
  title: string
  fields: FieldSpec[]
  initial: Record<string, unknown> | null
  busy: boolean
  onSubmit: (values: Record<string, unknown>) => void
  error?: string
}) {
  const editing = initial !== null
  const [values, setValues] = useState<Record<string, unknown>>({})

  // Re-seed the draft whenever the dialog opens for a different subject.
  const [seededFor, setSeededFor] = useState<unknown>(undefined)
  if (open && seededFor !== (initial ?? 'new')) {
    setSeededFor(initial ?? 'new')
    setValues(initial ?? {})
  }
  if (!open && seededFor !== undefined) setSeededFor(undefined)

  const ready = fields
    .filter((field) => field.required && !(editing && field.createOnly))
    .every((field) => String(values[field.key] ?? '').trim() !== '')

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={busy}
      title={title}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} disabled={!ready} onClick={() => onSubmit(values)}>
            {editing ? 'Save changes' : 'Create'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        {fields.map((field) => {
          if (editing && field.createOnly) return null
          if (field.kind === 'select') {
            return (
              <Select
                key={field.key}
                label={field.label}
                required={field.required}
                placeholder={field.required ? 'Select…' : 'Optional'}
                value={String(values[field.key] ?? '')}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field.key]: event.target.value }))
                }
                options={field.options ?? []}
              />
            )
          }
          if (field.kind === 'checkbox') {
            return (
              <Checkbox
                key={field.key}
                label={field.label}
                checked={Boolean(values[field.key])}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field.key]: event.target.checked }))
                }
              />
            )
          }
          return (
            <TextInput
              key={field.key}
              label={field.label}
              type={field.kind === 'number' ? 'number' : 'text'}
              required={field.required}
              value={String(values[field.key] ?? '')}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field.key]: event.target.value }))
              }
            />
          )
        })}
      </div>
    </Modal>
  )
}

function useCatalogueEditing(kind: CatalogueKind, singular: string) {
  const toast = useToast()
  const mutations = useCatalogueMutations(kind)
  const [dialog, setDialog] = useState<{ open: boolean; row: Record<string, unknown> | null }>({
    open: false,
    row: null,
  })
  const [error, setError] = useState<string | undefined>()

  const close = () => {
    setDialog({ open: false, row: null })
    setError(undefined)
  }

  const submit = (values: Record<string, unknown>) => {
    setError(undefined)
    const handlers = {
      onSuccess: () => {
        toast.success(dialog.row ? `${singular} updated` : `${singular} created`)
        close()
      },
      onError: (submitError: unknown) => {
        if (submitError instanceof ApiError) setError(submitError.displayMessage)
        toast.fromError(submitError, 'Could not save')
      },
    }
    if (dialog.row) {
      mutations.update.mutate({ id: dialog.row.id as UUID, payload: values }, handlers)
    } else {
      mutations.create.mutate(values, handlers)
    }
  }

  const remove = (id: UUID, label: string) => {
    if (!window.confirm(`Deactivate ${label}? It disappears from pickers; history keeps it.`)) {
      return
    }
    mutations.remove.mutate(id, {
      onSuccess: () => toast.success(`${singular} deactivated`),
      onError: (submitError) => toast.fromError(submitError, 'Still in use'),
    })
  }

  return {
    dialog,
    error,
    close,
    submit,
    remove,
    openCreate: () => setDialog({ open: true, row: null }),
    openEdit: (row: Record<string, unknown>) => setDialog({ open: true, row }),
    busy: mutations.create.isPending || mutations.update.isPending,
  }
}

/** The Edit/Deactivate cell shared by every catalogue table. */
function rowActions<T extends { id: UUID }>(
  mayEdit: boolean,
  mayDelete: boolean,
  editing: ReturnType<typeof useCatalogueEditing>,
  label: (row: T) => string,
): Array<Column<T>> {
  if (!mayEdit && !mayDelete) return []
  return [
    {
      key: 'actions',
      header: 'Actions',
      headerSrOnly: true,
      align: 'right',
      render: (row) => (
        <div className="flex justify-end gap-2">
          {mayEdit && (
            <Button
              size="sm"
              onClick={() => editing.openEdit(row as unknown as Record<string, unknown>)}
            >
              Edit
            </Button>
          )}
          {mayDelete && (
            <Button size="sm" variant="danger-soft" onClick={() => editing.remove(row.id, label(row))}>
              Deactivate
            </Button>
          )}
        </div>
      ),
    },
  ]
}

/* ================================================= the permission matrix */

function RoleMatrixDialog({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const toast = useToast()
  const catalog = useAccessCatalog()
  const cells = useRolePermissions(role?.id)
  const admin = useRoleAdmin()
  const [edits, setEdits] = useState<Map<string, number>>(new Map())
  const [error, setError] = useState<string | undefined>()

  const current = useMemo(() => {
    const map = new Map<string, PermissionCell>()
    for (const cell of cells.data ?? []) map.set(`${cell.resource}:${cell.action}`, cell)
    return map
  }, [cells.data])

  const close = () => {
    setEdits(new Map())
    setError(undefined)
    onClose()
  }

  const save = () => {
    if (!role) return
    setError(undefined)
    const payload = Array.from(edits.entries()).map(([key, scope]) => {
      const [resource, action] = key.split(':')
      return { resource: resource!, action: action!, scope }
    })
    admin.setPermissions.mutate(
      { id: role.id, cells: payload },
      {
        onSuccess: () => {
          toast.success('Permissions saved', 'Live for every holder of the role, immediately.')
          setEdits(new Map())
        },
        onError: (submitError) => {
          if (submitError instanceof ApiError) setError(submitError.displayMessage)
          toast.fromError(submitError, 'Could not save')
        },
      },
    )
  }

  const actions = catalog.data?.actions ?? []
  const resources = catalog.data?.resources ?? []

  return (
    <Modal
      open={Boolean(role)}
      onClose={close}
      busy={admin.setPermissions.isPending}
      size="xl"
      title={role ? `Permissions — ${role.name}` : ''}
      description="Scope per action: Self, Team, Department or All. Blank means denied."
      footer={
        <>
          <Button onClick={close} disabled={admin.setPermissions.isPending}>
            Close
          </Button>
          <Button
            variant="primary"
            loading={admin.setPermissions.isPending}
            disabled={edits.size === 0}
            onClick={save}
          >
            {edits.size > 0 ? `Save ${edits.size} change${edits.size === 1 ? '' : 's'}` : 'Save'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error && <Banner tone="danger">{error}</Banner>}
        {role?.is_read_only && (
          <Banner tone="info">
            A read-only role: the engine strips every write unconditionally, so only view and
            export scopes can take effect here.
          </Banner>
        )}
        {cells.isLoading || catalog.isLoading ? (
          <TableSkeleton columns={6} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr>
                  <th className="sticky left-0 bg-surface p-2 text-left font-semibold text-ink">
                    Resource
                  </th>
                  {actions.map((action) => (
                    <th key={action} className="p-2 text-left font-semibold text-ink-muted">
                      {humanize(action)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {resources.map((resource) => (
                  <tr key={resource} className="border-t border-line">
                    <td className="sticky left-0 whitespace-nowrap bg-surface p-2 font-medium text-ink">
                      {humanize(resource)}
                    </td>
                    {actions.map((action) => {
                      const key = `${resource}:${action}`
                      const scope = edits.has(key)
                        ? edits.get(key)!
                        : (current.get(key)?.scope ?? 0)
                      const customized = current.get(key)?.is_customized
                      return (
                        <td key={action} className="p-1">
                          <select
                            aria-label={`${resource} ${action}`}
                            className={`w-full rounded border bg-surface px-1 py-0.5 text-xs ${
                              edits.has(key)
                                ? 'border-brand text-ink'
                                : customized
                                  ? 'border-warning text-ink'
                                  : scope
                                    ? 'border-line text-ink'
                                    : 'border-transparent text-ink-subtle'
                            }`}
                            value={scope}
                            onChange={(event) => {
                              const next = new Map(edits)
                              const value = Number(event.target.value)
                              if (value === (current.get(key)?.scope ?? 0)) next.delete(key)
                              else next.set(key, value)
                              setEdits(next)
                            }}
                          >
                            {SCOPE_LABELS.map((label, value) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-ink-subtle">
          Amber borders mark cells an administrator customised earlier — re-seeding the defaults
          never touches those. Some grants are refused by design: user management, writes for
          read-only roles, and bulk import outside candidates.
        </p>
      </div>
    </Modal>
  )
}

/* ================================================================== page */

export function OrganisationPage() {
  const permissions = usePermissions()
  const toast = useToast()
  const [tab, setTab] = useState('departments')

  const departments = useDepartments()
  const designations = useDesignations({ enabled: permissions.can(RESOURCE.DESIGNATION) })
  const locations = useLocations({ enabled: permissions.can(RESOURCE.LOCATION) })
  const levels = useEmployeeLevels({ enabled: permissions.can(RESOURCE.DESIGNATION) })
  const roles = useRoles({ enabled: permissions.can(RESOURCE.ROLE) })

  const may = (resource: Parameters<typeof permissions.can>[0], action?: Parameters<typeof permissions.can>[1]) =>
    Boolean(permissions.can(resource, action))

  const departmentEditing = useCatalogueEditing('departments', 'Department')
  const designationEditing = useCatalogueEditing('designations', 'Designation')
  const locationEditing = useCatalogueEditing('locations', 'Location')
  const levelEditing = useCatalogueEditing('levels', 'Level')

  const roleAdmin = useRoleAdmin()
  const [roleDialog, setRoleDialog] = useState<{ open: boolean; row: Role | null }>({
    open: false,
    row: null,
  })
  const [roleError, setRoleError] = useState<string | undefined>()
  const [matrixRole, setMatrixRole] = useState<Role | null>(null)

  const departmentFields: FieldSpec[] = [
    { key: 'name', label: 'Name', kind: 'text', required: true },
    { key: 'code', label: 'Code', kind: 'text', required: true, createOnly: true },
    {
      key: 'kind',
      label: 'Function',
      kind: 'select',
      required: true,
      options: DEPARTMENT_KINDS.map((kind) => ({ value: kind, label: humanize(kind) })),
    },
    { key: 'description', label: 'Description', kind: 'text' },
  ]
  const designationFields: FieldSpec[] = [
    { key: 'title', label: 'Title', kind: 'text', required: true },
    {
      key: 'department',
      label: 'Department',
      kind: 'select',
      options: (departments.data ?? []).map((d) => ({ value: d.id, label: d.name })),
    },
    { key: 'description', label: 'Description', kind: 'text' },
  ]
  const locationFields: FieldSpec[] = [
    { key: 'name', label: 'Name', kind: 'text', required: true },
    { key: 'code', label: 'Code', kind: 'text', required: true, createOnly: true },
    { key: 'city', label: 'City', kind: 'text' },
    { key: 'state', label: 'State code', kind: 'text' },
    { key: 'pincode', label: 'Pincode', kind: 'text' },
    { key: 'is_head_office', label: 'Head office', kind: 'checkbox' },
  ]
  const levelFields: FieldSpec[] = [
    { key: 'name', label: 'Name', kind: 'text', required: true },
    { key: 'code', label: 'Code', kind: 'text', required: true, createOnly: true },
    { key: 'layer', label: 'Layer', kind: 'select', required: true, options: LAYERS },
    { key: 'rank', label: 'Rank (sort order)', kind: 'number', required: true },
  ]
  const roleFields: FieldSpec[] = [
    { key: 'code', label: 'Code (immutable)', kind: 'text', required: true, createOnly: true },
    { key: 'name', label: 'Name', kind: 'text', required: true },
    { key: 'layer', label: 'Layer', kind: 'select', required: true, options: LAYERS },
    { key: 'description', label: 'Description', kind: 'text' },
    { key: 'is_grantable', label: 'Grantable by HR', kind: 'checkbox' },
  ]

  const departmentColumns: Array<Column<Department>> = [
    { key: 'name', header: 'Department', render: (row) => <span className="font-medium">{row.name}</span> },
    { key: 'code', header: 'Code', secondary: true, render: (row) => row.code },
    { key: 'kind', header: 'Function', render: (row) => <Badge tone="brand">{row.kind}</Badge> },
    { key: 'head', header: 'Head', secondary: true, render: (row) => row.head_employee_name ?? '—' },
    ...rowActions<Department>(
      may(RESOURCE.DEPARTMENT, ACTION.EDIT),
      may(RESOURCE.DEPARTMENT, ACTION.DELETE),
      departmentEditing,
      (row) => row.name,
    ),
  ]
  const designationColumns: Array<Column<Designation>> = [
    { key: 'title', header: 'Designation', render: (row) => <span className="font-medium">{row.title}</span> },
    { key: 'department', header: 'Department', render: (row) => row.department_name ?? 'Any' },
    ...rowActions<Designation>(
      may(RESOURCE.DESIGNATION, ACTION.EDIT),
      may(RESOURCE.DESIGNATION, ACTION.DELETE),
      designationEditing,
      (row) => row.title,
    ),
  ]
  const locationColumns: Array<Column<Location>> = [
    { key: 'name', header: 'Location', render: (row) => <span className="font-medium">{row.name}</span> },
    { key: 'code', header: 'Code', secondary: true, render: (row) => row.code },
    { key: 'city', header: 'City', render: (row) => row.city || '—' },
    {
      key: 'head_office',
      header: 'Head office',
      render: (row) => (row.is_head_office ? <Badge tone="success">Yes</Badge> : '—'),
    },
    ...rowActions<Location>(
      may(RESOURCE.LOCATION, ACTION.EDIT),
      may(RESOURCE.LOCATION, ACTION.DELETE),
      locationEditing,
      (row) => row.name,
    ),
  ]
  const levelColumns: Array<Column<EmployeeLevel>> = [
    { key: 'name', header: 'Level', render: (row) => <span className="font-medium">{row.name}</span> },
    { key: 'code', header: 'Code', secondary: true, render: (row) => row.code },
    { key: 'layer', header: 'Layer', align: 'right', render: (row) => row.layer },
    { key: 'rank', header: 'Rank', align: 'right', secondary: true, render: (row) => row.rank },
    ...rowActions<EmployeeLevel>(
      may(RESOURCE.DESIGNATION, ACTION.EDIT),
      may(RESOURCE.DESIGNATION, ACTION.DELETE),
      levelEditing,
      (row) => row.name,
    ),
  ]

  const mayEditRoles = may(RESOURCE.ROLE, ACTION.EDIT)
  const roleColumns: Array<Column<Role>> = [
    { key: 'name', header: 'Role', render: (row) => <span className="font-medium">{row.name}</span> },
    { key: 'layer', header: 'Layer', align: 'right', render: (row) => row.layer },
    {
      key: 'flags',
      header: 'Properties',
      render: (row) => (
        <div className="flex flex-wrap gap-1">
          {row.is_read_only && <Badge tone="info">Read only</Badge>}
          {row.can_manage_users && <Badge tone="warning">Manages users</Badge>}
          {!row.is_system && <Badge tone="brand">Custom</Badge>}
          {!row.is_grantable && <Badge tone="danger">Not grantable</Badge>}
        </div>
      ),
    },
    {
      key: 'function',
      header: 'Function',
      secondary: true,
      render: (row) => row.department_kind || 'Any',
    },
    ...(mayEditRoles
      ? ([
          {
            key: 'actions',
            header: 'Actions',
            headerSrOnly: true,
            align: 'right',
            render: (row) => (
              <div className="flex justify-end gap-2">
                <Button size="sm" onClick={() => setMatrixRole(row)}>
                  Permissions
                </Button>
                <Button size="sm" onClick={() => setRoleDialog({ open: true, row })}>
                  Edit
                </Button>
                {!row.is_system && (
                  <Button
                    size="sm"
                    variant="danger-soft"
                    onClick={() => {
                      if (!window.confirm(`Deactivate the ${row.name} role?`)) return
                      roleAdmin.remove.mutate(row.id, {
                        onSuccess: () => toast.success('Role deactivated'),
                        onError: (e) => toast.fromError(e, 'Still held'),
                      })
                    }}
                  >
                    Deactivate
                  </Button>
                )}
              </div>
            ),
          },
        ] as Array<Column<Role>>)
      : []),
  ]

  const mayLetterhead = permissions.can(RESOURCE.ORG_SETTINGS)
  const tabs = [
    { id: 'departments', label: 'Departments', count: departments.data?.length },
    mayLetterhead && { id: 'letterhead', label: 'Letterhead' },
    permissions.can(RESOURCE.DESIGNATION) && {
      id: 'designations',
      label: 'Designations',
      count: designations.data?.length,
    },
    permissions.can(RESOURCE.LOCATION) && {
      id: 'locations',
      label: 'Locations',
      count: locations.data?.length,
    },
    permissions.can(RESOURCE.DESIGNATION) && {
      id: 'levels',
      label: 'Levels',
      count: levels.data?.length,
    },
    permissions.can(RESOURCE.ROLE) && { id: 'roles', label: 'Roles', count: roles.data?.length },
  ].filter(Boolean) as Array<{ id: string; label: string; count?: number }>

  const anyWrite =
    may(RESOURCE.DEPARTMENT, ACTION.CREATE) ||
    may(RESOURCE.LOCATION, ACTION.CREATE) ||
    mayEditRoles

  const addButton = (
    resource: Parameters<typeof permissions.can>[0],
    editing: ReturnType<typeof useCatalogueEditing>,
    label: string,
  ) =>
    may(resource, ACTION.CREATE) ? (
      <div className="mb-3 flex justify-end">
        <Button variant="primary" size="sm" onClick={editing.openCreate}>
          {label}
        </Button>
      </div>
    ) : null

  return (
    <>
      <PageHeader
        title="Organisation"
        description="Departments, designations, locations, seniority levels and the role catalogue."
      />

      {!anyWrite && (
        <Banner tone="info" title="Reference data">
          You can read the structure here; changing it is an administrative permission.
        </Banner>
      )}

      <Section>
        <div className="space-y-5">
          <Tabs active={tab} onChange={setTab} items={tabs} />

          <TabPanel id="departments" active={tab}>
            {addButton(RESOURCE.DEPARTMENT, departmentEditing, 'Add department')}
            {departments.isError ? (
              <ErrorState error={departments.error} />
            ) : (
              <DataTable
                caption="Departments"
                columns={departmentColumns}
                rows={departments.data ?? []}
                rowKey={(row) => row.id}
                isLoading={departments.isLoading}
                loadingState={<TableSkeleton columns={5} />}
                emptyState={<EmptyState title="No departments" />}
              />
            )}
          </TabPanel>

          {mayLetterhead && (
            <TabPanel id="letterhead" active={tab}>
              <LetterheadTab />
            </TabPanel>
          )}

          <TabPanel id="designations" active={tab}>
            {addButton(RESOURCE.DESIGNATION, designationEditing, 'Add designation')}
            <DataTable
              caption="Designations"
              columns={designationColumns}
              rows={designations.data ?? []}
              rowKey={(row) => row.id}
              isLoading={designations.isLoading}
              loadingState={<TableSkeleton columns={3} />}
              emptyState={<EmptyState title="No designations" />}
            />
          </TabPanel>

          <TabPanel id="locations" active={tab}>
            {addButton(RESOURCE.LOCATION, locationEditing, 'Add location')}
            <DataTable
              caption="Locations"
              columns={locationColumns}
              rows={locations.data ?? []}
              rowKey={(row) => row.id}
              isLoading={locations.isLoading}
              loadingState={<TableSkeleton columns={5} />}
              emptyState={<EmptyState title="No locations" />}
            />
          </TabPanel>

          <TabPanel id="levels" active={tab}>
            {addButton(RESOURCE.DESIGNATION, levelEditing, 'Add level')}
            <DataTable
              caption="Seniority levels"
              columns={levelColumns}
              rows={levels.data ?? []}
              rowKey={(row) => row.id}
              isLoading={levels.isLoading}
              loadingState={<TableSkeleton columns={5} />}
              emptyState={<EmptyState title="No levels" />}
            />
          </TabPanel>

          <TabPanel id="roles" active={tab}>
            <Card className="mb-4">
              <CardHeader
                title="The role catalogue"
                description={
                  mayEditRoles
                    ? 'Permissions edited here are live immediately and survive re-seeding. ' +
                      'System roles keep their structure; custom roles are fully yours.'
                    : "Each role's permissions are enforced server-side."
                }
                action={
                  mayEditRoles && (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => setRoleDialog({ open: true, row: null })}
                    >
                      New role
                    </Button>
                  )
                }
              />
            </Card>
            <DataTable
              caption="Roles"
              columns={roleColumns}
              rows={roles.data ?? []}
              rowKey={(row) => row.id}
              isLoading={roles.isLoading}
              loadingState={<TableSkeleton columns={5} />}
              emptyState={<EmptyState title="No roles visible" />}
            />
          </TabPanel>
        </div>
      </Section>

      {/* ------------------------------------------------------- dialogs */}
      <CatalogueDialog
        open={departmentEditing.dialog.open}
        onClose={departmentEditing.close}
        title={departmentEditing.dialog.row ? 'Edit department' : 'New department'}
        fields={departmentFields}
        initial={departmentEditing.dialog.row}
        busy={departmentEditing.busy}
        onSubmit={departmentEditing.submit}
        error={departmentEditing.error}
      />
      <CatalogueDialog
        open={designationEditing.dialog.open}
        onClose={designationEditing.close}
        title={designationEditing.dialog.row ? 'Edit designation' : 'New designation'}
        fields={designationFields}
        initial={designationEditing.dialog.row}
        busy={designationEditing.busy}
        onSubmit={(values) =>
          designationEditing.submit({ ...values, department: values.department || null })
        }
        error={designationEditing.error}
      />
      <CatalogueDialog
        open={locationEditing.dialog.open}
        onClose={locationEditing.close}
        title={locationEditing.dialog.row ? 'Edit location' : 'New location'}
        fields={locationFields}
        initial={locationEditing.dialog.row}
        busy={locationEditing.busy}
        onSubmit={locationEditing.submit}
        error={locationEditing.error}
      />
      <CatalogueDialog
        open={levelEditing.dialog.open}
        onClose={levelEditing.close}
        title={levelEditing.dialog.row ? 'Edit level' : 'New level'}
        fields={levelFields}
        initial={levelEditing.dialog.row}
        busy={levelEditing.busy}
        onSubmit={(values) =>
          levelEditing.submit({ ...values, layer: Number(values.layer), rank: Number(values.rank) })
        }
        error={levelEditing.error}
      />
      <CatalogueDialog
        open={roleDialog.open}
        onClose={() => {
          setRoleDialog({ open: false, row: null })
          setRoleError(undefined)
        }}
        title={roleDialog.row ? `Edit role — ${roleDialog.row.name}` : 'New role'}
        fields={roleFields}
        initial={roleDialog.row as unknown as Record<string, unknown> | null}
        busy={roleAdmin.create.isPending || roleAdmin.update.isPending}
        error={roleError}
        onSubmit={(values) => {
          setRoleError(undefined)
          const payload = { ...values, layer: Number(values.layer) }
          const handlers = {
            onSuccess: () => {
              toast.success(roleDialog.row ? 'Role updated' : 'Role created')
              setRoleDialog({ open: false, row: null })
            },
            onError: (submitError: unknown) => {
              if (submitError instanceof ApiError) setRoleError(submitError.displayMessage)
              toast.fromError(submitError, 'Could not save')
            },
          }
          if (roleDialog.row) {
            roleAdmin.update.mutate({ id: roleDialog.row.id, payload }, handlers)
          } else {
            roleAdmin.create.mutate(payload, handlers)
          }
        }}
      />
      <RoleMatrixDialog role={matrixRole} onClose={() => setMatrixRole(null)} />
    </>
  )
}


/**
 * The organisation's letterhead: what generated documents (offer letters
 * today) carry — name, legal name, signatory, logo and signature. Uploading
 * here changes every FUTURE letter; letters already sent stay as sent.
 */
function LetterheadTab() {
  const toast = useToast()
  const settings = useOrgSettings()
  const update = useUpdateOrgSettings()
  const [values, setValues] = useState({
    name: '',
    legal_name: '',
    signatory_name: '',
    signatory_designation: '',
  })
  const [logo, setLogo] = useState<File | null>(null)
  const [signature, setSignature] = useState<File | null>(null)
  const [loaded, setLoaded] = useState(false)

  if (settings.data && !loaded) {
    setValues({
      name: settings.data.name,
      legal_name: settings.data.legal_name,
      signatory_name: settings.data.signatory_name,
      signatory_designation: settings.data.signatory_designation,
    })
    setLoaded(true)
  }

  if (settings.isLoading) return <TableSkeleton columns={2} />

  const set = (key: keyof typeof values) =>
    (event: React.ChangeEvent<HTMLInputElement>) =>
      setValues((current) => ({ ...current, [key]: event.target.value }))

  return (
    <Card className="max-w-2xl space-y-4">
      <CardHeader
        title="Letterhead & signatory"
        description="Used on generated documents such as offer letters. Letters already sent keep the letterhead they were sent with."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Organisation name" value={values.name} onChange={set('name')} />
        <TextInput
          label="Legal name"
          value={values.legal_name}
          onChange={set('legal_name')}
          description="As it should appear on formal letters."
        />
        <TextInput label="Signatory name" value={values.signatory_name} onChange={set('signatory_name')} />
        <TextInput
          label="Signatory designation"
          value={values.signatory_designation}
          onChange={set('signatory_designation')}
        />
        <TextInput
          label={`Logo${settings.data?.has_logo ? ' (uploaded ✓)' : ''}`}
          type="file"
          accept="image/*"
          onChange={(event) => setLogo(event.target.files?.[0] ?? null)}
          description="Shown at the top of generated letters."
        />
        <TextInput
          label={`Signature image${settings.data?.has_signature ? ' (uploaded ✓)' : ''}`}
          type="file"
          accept="image/*"
          onChange={(event) => setSignature(event.target.files?.[0] ?? null)}
          description="Stamped above the signatory's name."
        />
      </div>
      <div>
        <Button
          variant="primary"
          loading={update.isPending}
          onClick={() =>
            update.mutate(
              { ...values, logo, signature },
              {
                onSuccess: () => {
                  toast.success('Letterhead saved')
                  setLogo(null)
                  setSignature(null)
                },
                onError: (error) => toast.fromError(error, 'Could not save'),
              },
            )
          }
        >
          Save letterhead
        </Button>
      </div>
    </Card>
  )
}
