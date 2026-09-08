/**
 * Employees: list, atomic creation, detail, and the self-service profile.
 *
 * Creation posts the whole payload in one call, because the API creates the
 * person, their login and their role in ONE transaction and rejects a partial
 * payload outright. A multi-step wizard that saved as it went would be
 * modelling a state the backend refuses to have.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import {
  PagePager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { FieldRow, Select, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import {
  useCreateEmployee,
  useDepartments,
  useDesignations,
  useEmployees,
  useLocations,
  useRoles,
} from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { roleLabel } from '@/app/AppShell'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import type {
  EmployeeCreatePayload,
  EmployeeCreateResult,
  EmployeeListItem,
  EmployeeStatus,
  RoleCode,
} from '@/lib/types'

const STATUS_TONES: Record<EmployeeStatus, Tone> = {
  onboarding: 'info',
  on_probation: 'warning',
  active: 'success',
  confirmed: 'success',
  on_leave: 'info',
  on_notice: 'warning',
  resigned: 'neutral',
  terminated: 'danger',
  exited: 'neutral',
}

/* ---------------------------------------------------------------- create */

function CreateEmployeeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const create = useCreateEmployee()

  const departments = useDepartments()
  const designations = useDesignations()
  const locations = useLocations()
  const roles = useRoles()
  // NOT `status: 'active'`. In this system "active" is one status among
  // several that mean "employed here today" — a confirmed employee has moved
  // PAST it, and in a mature org almost everyone is confirmed. Filtering on
  // it emptied the manager list for exactly the people who should be in it.
  // Fetch everyone the caller may see and exclude only those the server would
  // refuse (exited, or without a login and role).
  const managers = useEmployees({ page_size: '200' })

  const [values, setValues] = useState({
    first_name: '',
    last_name: '',
    email: '',
    personal_email: '',
    phone: '',
    role_code: '',
    department_id: '',
    designation_id: '',
    location_id: '',
    reporting_manager_id: '',
    date_of_joining: '',
    annual_ctc: '',
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [result, setResult] = useState<EmployeeCreateResult | null>(null)

  useEffect(() => {
    if (open) setErrors({})
  }, [open])

  function set(key: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [key]: value }))
    setErrors(({ [key]: _removed, ...rest }) => rest)
  }

  /*
   * Roles are filtered to those the API will actually accept: grantable, and
   * requiring an employee record. `admin` and `ceo` are system principals with
   * no Employee, and `create_employee` refuses them by design.
   */
  const assignableRoles = (roles.data ?? []).filter(
    (role) => role.is_grantable && role.requires_employee,
  )

  const department = (departments.data ?? []).find((item) => item.id === values.department_id)
  const roleFitsDepartment = (roleCode: string) => {
    const role = assignableRoles.find((item) => item.code === roleCode)
    if (!role?.department_kind || !department) return true
    return role.department_kind === department.kind
  }

  /*
   * The role list is the FULL set of roles the caller may assign; the server
   * decides that (`/roles/` is already scoped) and the seniority rule
   * (`assert_creator_may_grant`) refuses on submit. What the form does is
   * present it honestly: with a department chosen, only that function's roles
   * plus the department-agnostic ones are OFFERED. Greying the rest out —
   * the previous behaviour — read as "roles are missing", when what was
   * missing was the explanation.
   */
  const offeredRoles = department
    ? assignableRoles.filter((role) => roleFitsDepartment(role.code))
    : assignableRoles

  const selectedRole = assignableRoles.find((role) => role.code === values.role_code)
  const layerOfRoleCode = new Map((roles.data ?? []).map((role) => [role.code, role.layer]))

  /*
   * The manager list mirrors `assert_reporting_manager_is_valid` so the form
   * offers only what the server accepts:
   *   1. active, not exited, holds a login and a role;
   *   2. at the SAME level or a more senior one — only a junior manager is
   *      refused, because a line running downward inverts every approval;
   *   3. any department — cross-functional reporting lines are supported.
   * Until a role is chosen it shows everyone eligible in principle, so the
   * field is never mysteriously empty.
   */
  const eligibleManagers = (managers.data?.data ?? []).filter((candidate) => {
    if (candidate.status === 'exited' || candidate.status === 'terminated') return false
    const layers = candidate.roles
      .map((code) => layerOfRoleCode.get(code))
      .filter((layer): layer is number => typeof layer === 'number')
    if (layers.length === 0) return false // no role → approvals would go nowhere
    const managerLayer = Math.min(...layers)
    // Same level or higher. A peer manager grants no extra authority — the
    // ROLE decides permission, not the reporting line — so the only direction
    // that has to stay closed is downward.
    if (selectedRole && managerLayer > selectedRole.layer) return false
    return true
  })

  //: Heads (layer 1-2) answer to the CEO and need no manager; everyone else
  //: must have one — the server enforces it, this makes the form say so.
  const managerRequired = Boolean(selectedRole && selectedRole.layer > 2)

  const ready =
    values.first_name &&
    values.email &&
    values.personal_email &&
    values.role_code &&
    values.department_id &&
    // Every new employee carries a job title: it is what the welcome email
    // announces and what appears against them everywhere afterwards. The API
    // requires it too, so this only saves a round trip.
    values.designation_id &&
    values.date_of_joining &&
    (!managerRequired || values.reporting_manager_id)

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        busy={create.isPending}
        size="lg"
        title="Add employee"
        description="Creates the person, their login and their role in one transaction — all of it, or none of it."
        footer={
          <>
            <Button onClick={onClose} disabled={create.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={!ready}
              onClick={() => {
                setErrors({})
                // Seniority level is not part of employee creation any more:
                // the field is gone from the form and the payload alike, and
                // the backend treats it as fully optional.
                const payload: EmployeeCreatePayload = {
                  first_name: values.first_name,
                  last_name: values.last_name || undefined,
                  email: values.email,
                  personal_email: values.personal_email,
                  phone: values.phone || undefined,
                  role_code: values.role_code as RoleCode,
                  department_id: values.department_id,
                  designation_id: values.designation_id,
                  location_id: values.location_id || undefined,
                  reporting_manager_id: values.reporting_manager_id || undefined,
                  date_of_joining: values.date_of_joining,
                  annual_ctc: values.annual_ctc || undefined,
                }
                create.mutate(payload, {
                  onSuccess: (created) => {
                    setResult(created)
                    onClose()
                    toast.success('Employee created', created.employee.employee_code)
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not create the employee')
                  },
                })
              }}
            >
              Create employee
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Banner tone="info" title="Nothing is created halfway">
            If any rule fails — the role does not suit the department, the reporting manager is not
            senior enough, or you are not permitted to grant that role — the entire creation is
            rolled back and no partial account is left behind.
          </Banner>

          <FieldRow>
            <TextInput
              label="First name"
              required
              value={values.first_name}
              onChange={(event) => set('first_name', event.target.value)}
              error={errors.first_name}
            />
            <TextInput
              label="Last name"
              value={values.last_name}
              onChange={(event) => set('last_name', event.target.value)}
              error={errors.last_name}
            />
          </FieldRow>

          <FieldRow>
            <TextInput
              label="Company Email / HRMS Login Email"
              type="email"
              required
              value={values.email}
              onChange={(event) => set('email', event.target.value)}
              error={errors.email}
              description="The employee's HRMS account email. Either this or their Personal Email signs them in."
            />
            <TextInput
              label="Personal Email"
              type="email"
              required
              value={values.personal_email}
              onChange={(event) => set('personal_email', event.target.value)}
              error={errors.personal_email}
              description="The login credentials and setup instructions are sent ONLY here — never to the company address. It can also be used to sign in."
            />
          </FieldRow>

          <TextInput
            label="Phone"
            value={values.phone}
            onChange={(event) => set('phone', event.target.value)}
            error={errors.phone}
          />

          <FieldRow>
            <Select
              label="Department"
              required
              placeholder="Select a department"
              value={values.department_id}
              onChange={(event) => set('department_id', event.target.value)}
              options={(departments.data ?? []).map((item) => ({
                value: item.id,
                label: `${item.name} (${item.kind})`,
              }))}
              error={errors.department}
            />
            <Select
              label="Role"
              required
              placeholder="Select a role"
              value={values.role_code}
              onChange={(event) => set('role_code', event.target.value)}
              options={offeredRoles.map((role) => ({
                value: role.code,
                label: `${role.name} · L${role.layer}`,
              }))}
              error={errors.role || errors.role_code}
              description={
                (department
                  ? `${offeredRoles.length} roles fit the ${department.kind} function. `
                  : '') +
                'System-level responsibility: what this person can see and do in the HRMS (e.g. HR Head, Recruiter, Operations Manager).'
              }
            />
          </FieldRow>

          <FieldRow>
            <Select
              label="Designation"
              required
              placeholder="Select a designation"
              value={values.designation_id}
              onChange={(event) => set('designation_id', event.target.value)}
              options={(designations.data ?? [])
                .filter(
                  (item) =>
                    !item.department ||
                    !values.department_id ||
                    item.department === values.department_id,
                )
                .map((item) => ({ value: item.id, label: item.title }))}
              error={errors.designation ?? errors.designation_id}
              description={
                'The actual job title in the organization (e.g. Doctor, Customer Relationship Manager, Accountant). ' +
                (values.department_id
                  ? 'Only titles valid for the chosen department are offered.'
                  : 'Choose a department first to narrow this list.')
              }
            />
            <Select
              label="Location"
              placeholder="Optional"
              value={values.location_id}
              onChange={(event) => set('location_id', event.target.value)}
              options={(locations.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
              error={errors.location}
            />
          </FieldRow>

          <FieldRow>
            {/*
              Visible to EVERY creator — hiding it for Admin left the form
              unable to satisfy the server's own rule that a non-head
              employee must have a manager. REQUIRED below head level, and
              OPTIONAL but allowed for heads: a second HR Head reporting to
              the first, or to a Medical Director, is a real structure the
              form has to be able to record.
            */}
            <Select
              label="Reporting manager"
              required={managerRequired}
              placeholder={managerRequired ? 'Select a manager' : 'Optional for a Head role'}
              value={values.reporting_manager_id}
              onChange={(event) => set('reporting_manager_id', event.target.value)}
              options={eligibleManagers.map((item) => ({
                value: item.id,
                label: `${item.full_name} — ${item.designation_title || 'no title'}${
                  item.department_name ? ` · ${item.department_name}` : ''
                }`,
              }))}
              error={errors.reporting_manager}
              description={
                managers.isLoading
                  ? 'Loading people…'
                  : !selectedRole
                    ? 'Pick a role first — heads may leave this empty; everyone else needs one.'
                    : !managerRequired
                      ? `Optional: a head may answer to the CEO, who has no record here — or to a peer or senior. ${eligibleManagers.length} eligible.`
                      : eligibleManagers.length === 0
                        ? `Nobody at or above a ${selectedRole.name} exists yet — create that person first.`
                        : `${eligibleManagers.length} eligible: anyone at the same level or higher, from any department.`
              }
            />
            <TextInput
              label="Date of joining"
              type="date"
              required
              value={values.date_of_joining}
              onChange={(event) => set('date_of_joining', event.target.value)}
              error={errors.date_of_joining}
            />
          </FieldRow>

          <TextInput
            label="Annual salary (CTC)"
            type="number"
            min={0}
            placeholder="e.g. 480000"
            value={values.annual_ctc}
            onChange={(event) => set('annual_ctc', event.target.value)}
            error={errors.annual_ctc}
            description="The agreed yearly CTC in ₹. Visible only to HR Head, Finance and Admin; the payroll module remains the authority for actual pay."
          />

          {Object.entries(errors)
            .filter(
              ([key]) =>
                ![
                  'first_name', 'last_name', 'email', 'personal_email', 'phone',
                  'role', 'role_code', 'department', 'designation', 'level',
                  'location', 'reporting_manager', 'date_of_joining', 'annual_ctc',
                ].includes(key),
            )
            .map(([key, message]) => (
              <Banner key={key} tone="danger" title="The server refused this">
                {message}
              </Banner>
            ))}
        </div>
      </Modal>

      <Modal
        open={result !== null}
        onClose={() => setResult(null)}
        size="sm"
        title="Employee created"
        description={`${result?.employee.full_name ?? ''} can now sign in.`}
        footer={
          <>
            {result && (
              <Link to={`/employees/${result.employee.id}`}>
                <Button variant="primary">Open record</Button>
              </Link>
            )}
            <Button onClick={() => setResult(null)}>Done</Button>
          </>
        }
      >
        {result && (
          <div className="space-y-4">
            <DescriptionList
              columns={1}
              items={[
                { label: 'Employee code', value: result.employee.employee_code },
                { label: 'Sign-in email', value: result.user.email },
                { label: 'Role granted', value: roleLabel(result.role) },
              ]}
            />
            {result.welcome_email_sent === false ? (
              <Banner tone="danger" title="The welcome email did NOT send">
                The account exists and a temporary password was set, but the email to{' '}
                <strong>{result.user.email}</strong> failed — they cannot sign in until they have
                it. The failure is recorded in the audit log as{' '}
                <code className="mx-1">welcome_email_failed</code>. Fix the mail configuration,
                then open this employee's profile and use{' '}
                <strong>Resend sign-in details</strong> to issue fresh credentials.
              </Banner>
            ) : (
              <Banner tone="success" title="Sign-in details sent">
                A temporary password was generated and emailed to{' '}
                <strong>{result.user.email}</strong> with a link to sign in. It works once: on their
                first sign-in they must choose their own password before they can do anything else,
                and the temporary one stops working the moment they do. It is not shown here and no
                endpoint returns it. If they tell you it never arrived, check their spam folder
                first, then use <strong>Resend sign-in details</strong> on their profile.
              </Banner>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}

/* ------------------------------------------------------------------ list */

export function EmployeesPage() {
  const navigate = useNavigate()
  const permissions = usePermissions()
  const list = useListParams({ ordering: 'employee_code' })
  const query = useEmployees(list.queryParams)
  const departments = useDepartments({ enabled: permissions.can(RESOURCE.DEPARTMENT) })
  const [creating, setCreating] = useState(false)

  const rows = query.data?.data ?? []
  const mayCreate =
    permissions.can(RESOURCE.EMPLOYEE, ACTION.CREATE) && permissions.can(RESOURCE.USER, ACTION.CREATE)
  const scope = permissions.scopeOf(RESOURCE.EMPLOYEE)

  const columns: Array<Column<EmployeeListItem>> = [
    {
      key: 'name',
      header: 'Employee',
      sortKey: 'first_name',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.full_name} size="sm" />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.full_name}</p>
            <p className="truncate text-xs text-ink-muted">{row.employee_code}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'department',
      header: 'Department',
      secondary: true,
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate text-ink">{row.department_name ?? '—'}</p>
          <p className="truncate text-xs text-ink-muted">{row.designation_title || '—'}</p>
        </div>
      ),
    },
    {
      key: 'roles',
      header: 'Roles',
      secondary: true,
      render: (row) => (
        <div className="flex flex-wrap gap-1">
          {row.roles.length ? (
            row.roles.map((code) => (
              <Badge key={code} tone="brand">
                {roleLabel(code)}
              </Badge>
            ))
          ) : (
            <span className="text-xs text-ink-subtle">No login</span>
          )}
        </div>
      ),
    },
    {
      key: 'manager',
      header: 'Reports to',
      secondary: true,
      render: (row) => row.reporting_manager_name || '—',
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <Badge tone={STATUS_TONES[row.status] ?? 'neutral'}>{row.status.replace(/_/g, ' ')}</Badge>
      ),
    },
    {
      key: 'joined',
      header: 'Joined',
      sortKey: 'date_of_joining',
      align: 'right',
      secondary: true,
      render: (row) => (
        <span className="text-xs text-ink-muted">{formatDate(row.date_of_joining)}</span>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Employees"
        description={
          scope === 'all'
            ? 'Everyone in the organisation.'
            : scope === 'department'
              ? 'Your department and the departments beneath it.'
              : scope === 'team'
                ? 'Your reporting line.'
                : 'Your own record.'
        }
        actions={
          mayCreate && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              Add employee
            </Button>
          )
        }
      />

      <Section>
        <div className="space-y-4">
          <TableToolbar
            search={list.searchDraft}
            onSearchChange={list.setSearchDraft}
            searchPlaceholder="Search name, code or email…"
            onReset={list.reset}
            hasFilters={list.hasFilters}
          >
            {(departments.data?.length ?? 0) > 0 && (
              <FilterSelect
                label="Department"
                value={list.filters.department ?? ''}
                onChange={(value) => list.setParam('department', value)}
                options={(departments.data ?? []).map((item) => ({
                  value: item.id,
                  label: item.name,
                }))}
                allLabel="All departments"
              />
            )}
            <FilterSelect
              label="Status"
              value={list.filters.status ?? ''}
              onChange={(value) => list.setParam('status', value)}
              options={[
                { value: 'active', label: 'Active' },
                { value: 'on_notice', label: 'On notice' },
                { value: 'on_leave', label: 'On leave' },
                { value: 'exited', label: 'Exited' },
              ]}
              allLabel="All statuses"
            />
          </TableToolbar>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Employees"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/employees/${row.id}`)}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={6} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No employees match' : 'No employees visible'}
                  description="What you see here is scoped by your role, and the server decides that scope."
                />
              }
              footer={
                <PagePager
                  page={query.data?.meta.page}
                  pages={query.data?.meta.pages}
                  count={query.data?.meta.count}
                  pageSize={query.data?.meta.page_size}
                  onPage={list.setPage}
                  isFetching={query.isFetching}
                />
              }
            />
          )}
        </div>
      </Section>

      <CreateEmployeeModal open={creating} onClose={() => setCreating(false)} />
    </>
  )
}

/* ---------------------------------------------------------------- detail */
