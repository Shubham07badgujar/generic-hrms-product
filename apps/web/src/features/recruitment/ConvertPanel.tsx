/**
 * Candidate → Employee: the onboarding form.
 *
 * Gated on EMPLOYEE/CREATE, not on any recruitment permission — a recruiter
 * runs the entire pipeline and still cannot mint an employee. The conversion
 * itself delegates to the same `create_employee` service the manual path uses,
 * so every hierarchy rule applies: the target role must suit the department,
 * the designation must belong to the department, the reporting manager must be
 * senior enough, and person + login + role commit or roll back together.
 *
 * HR Head verifies nine things — name, work email, phone, department,
 * designation, location, reporting manager, joining date — all pre-filled from
 * the candidate, the job opening and the offer. The role
 * are deliberately NOT on this form: they come from the job's target role and
 * the offer, decided when the job was authored.
 *
 * The temporary password is shown ONCE, in the response to this call. It is
 * never returned by any other endpoint, so the UI makes that explicit rather
 * than letting someone navigate away and lose it.
 */

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, DescriptionList } from '@/components/ui/Card'
import { Modal } from '@/components/ui/Modal'
import { FieldRow, Select, TextInput } from '@/components/ui/Field'
import { Banner } from '@/components/ui/Misc'
import { useToast } from '@/components/ui/Toast'
import {
  useConvertToEmployee,
  useCurrentEmployees,
  useDepartments,
  useDesignations,
  useLocations,
} from '@/lib/queries'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { Link } from 'react-router-dom'
import type { Application, ConversionResult } from '@/lib/types'

const EMPTY = {
  first_name: '',
  last_name: '',
  email: '',
  personal_email: '',
  phone: '',
  department: '',
  designation: '',
  location: '',
  reporting_manager: '',
  date_of_joining: '',
}

export function ConvertPanel({
  application,
  onConverted,
}: {
  application: Application
  onConverted?: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const convert = useConvertToEmployee(application.id)

  const departments = useDepartments({ enabled: permissions.can(RESOURCE.DEPARTMENT) })
  const designations = useDesignations()
  const locations = useLocations()
  const managers = useCurrentEmployees()

  const [open, setOpen] = useState(false)
  const [values, setValues] = useState(EMPTY)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [result, setResult] = useState<ConversionResult | null>(null)

  // The form opens pre-filled from the candidate, the job and the offer —
  // HR verifies rather than retypes.
  useEffect(() => {
    if (!open) return
    const defaults = application.conversion_defaults
    setFieldErrors({})
    setValues({
      first_name: defaults?.first_name ?? '',
      last_name: defaults?.last_name ?? '',
      // The company email is ENTERED by HR, never assumed: the address the
      // candidate applied with is their personal one and must not become
      // the login.
      email: '',
      personal_email: (defaults as { personal_email?: string } | null)?.personal_email ?? defaults?.email ?? '',
      phone: defaults?.phone ?? '',
      department: defaults?.department ?? '',
      designation: defaults?.designation ?? '',
      location: defaults?.location ?? '',
      reporting_manager: defaults?.reporting_manager ?? '',
      date_of_joining: defaults?.date_of_joining ?? '',
    })
  }, [open, application])

  function set(key: keyof typeof EMPTY, value: string) {
    setValues((current) => ({ ...current, [key]: value }))
    setFieldErrors(({ [key]: _removed, ...rest }) => rest)
  }

  const mayConvert = permissions.can(RESOURCE.EMPLOYEE, ACTION.CREATE)

  // Same rule the job form applies: a designation bound to a department is
  // only offered when it matches; the server refuses the combination anyway.
  const eligibleDesignations = (designations.data ?? []).filter(
    (item) => !item.department || !values.department || item.department === values.department,
  )

  const ready =
    values.first_name && values.email && values.personal_email && values.date_of_joining

  if (application.status === 'hired') {
    return (
      <Card className="space-y-4">
        <CardHeader title="Hired" description="This candidate is now an employee." />
        <Banner tone="success" title="Conversion complete">
          The employee record links back to this candidate, which also makes them permanently
          ineligible for the data-retention purge.
        </Banner>
      </Card>
    )
  }

  if (application.status !== 'offer_accepted') return null

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Onboard as employee"
        description="Verify the joiner's details — then the person, their login and their role are created in one transaction."
        action={
          mayConvert && (
            <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
              Start onboarding
            </Button>
          )
        }
      />

      {!mayConvert && (
        <Banner tone="info">
          Converting a candidate creates a user account, so it is restricted to HR Head, HR Manager
          and Admin.
        </Banner>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        busy={convert.isPending}
        size="lg"
        title="Onboard as employee"
        description={`${application.candidate_name} — ${application.job_title}`}
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={convert.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={convert.isPending}
              disabled={!ready}
              onClick={() => {
                setFieldErrors({})
                convert.mutate(
                  {
                    first_name: values.first_name,
                    last_name: values.last_name,
                    email: values.email,
                    personal_email: values.personal_email,
                    phone: values.phone,
                    ...(values.department ? { department: values.department } : {}),
                    ...(values.designation ? { designation: values.designation } : {}),
                    ...(values.location ? { location: values.location } : {}),
                    ...(values.reporting_manager
                      ? { reporting_manager: values.reporting_manager }
                      : {}),
                    ...(values.date_of_joining ? { date_of_joining: values.date_of_joining } : {}),
                  },
                  {
                    onSuccess: (created) => {
                      setResult(created)
                      setOpen(false)
                      toast.success('Employee created', `Code ${created.employee_code}`)
                      onConverted?.()
                    },
                    onError: (error) => {
                      if (error instanceof ApiError) setFieldErrors(error.fieldErrors)
                      toast.fromError(error, 'Conversion failed')
                    },
                  },
                )
              }}
            >
              Create employee
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Banner tone="info" title="Pre-filled from the application">
            Everything below comes from the candidate, the job opening and the offer — verify and
            correct where needed. The role comes from the job itself.
          </Banner>

          <FieldRow>
            <TextInput
              label="First name"
              required
              value={values.first_name}
              onChange={(event) => set('first_name', event.target.value)}
              error={fieldErrors.first_name}
            />
            <TextInput
              label="Last name"
              value={values.last_name}
              onChange={(event) => set('last_name', event.target.value)}
              error={fieldErrors.last_name}
            />
          </FieldRow>

          <FieldRow>
            <TextInput
              label="Company Email / HRMS Login Email"
              required
              type="email"
              placeholder="e.g. name@yourcompany.com"
              value={values.email}
              onChange={(event) => set('email', event.target.value)}
              error={fieldErrors.email}
              description="This is the email the employee must use to log into HRMS — their only login."
            />
            <TextInput
              label="Personal Email"
              required
              type="email"
              value={values.personal_email}
              onChange={(event) => set('personal_email', event.target.value)}
              error={fieldErrors.personal_email}
              description="Pre-filled from the application. The password setup email goes here; it can never log in."
            />
          </FieldRow>

          <TextInput
            label="Phone number"
            value={values.phone}
            onChange={(event) => set('phone', event.target.value)}
            error={fieldErrors.phone}
          />

          <FieldRow>
            <Select
              label="Department"
              value={values.department}
              onChange={(event) => set('department', event.target.value)}
              options={(departments.data ?? []).map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              error={fieldErrors.department}
            />
            <Select
              label="Designation"
              value={values.designation}
              onChange={(event) => set('designation', event.target.value)}
              options={eligibleDesignations.map((item) => ({
                value: item.id,
                label: item.title,
              }))}
              error={fieldErrors.designation}
              description="Must belong to the department; the server enforces this."
            />
          </FieldRow>

          <FieldRow>
            <Select
              label="Location"
              value={values.location}
              onChange={(event) => set('location', event.target.value)}
              options={(locations.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
              error={fieldErrors.location}
            />
            <Select
              label="Reporting manager"
              value={values.reporting_manager}
              onChange={(event) => set('reporting_manager', event.target.value)}
              options={managers.rows.map((item) => ({
                value: item.id,
                label: `${item.full_name} — ${item.designation_title || item.department_name || ''}`,
              }))}
              error={fieldErrors.reporting_manager}
              description="Must be senior enough for the target role; the server checks this."
            />
          </FieldRow>

          <TextInput
            label="Date of joining"
            required
            type="date"
            value={values.date_of_joining}
            onChange={(event) => set('date_of_joining', event.target.value)}
            error={fieldErrors.date_of_joining}
          />

          {/* Field-attributed hierarchy errors land on the inputs above; anything
              else the service objects to is surfaced here in full. */}
          {Object.entries(fieldErrors)
            .filter(([key]) => !(key in EMPTY))
            .map(([key, message]) => (
              <Banner key={key} tone="danger" title="The server refused this conversion">
                {message}
              </Banner>
            ))}
        </div>
      </Modal>

      {/* The one and only sighting of the temporary password. */}
      <Modal
        open={result !== null}
        onClose={() => setResult(null)}
        title="Employee created"
        description={`${application.candidate_name} now has an employment record and a login.`}
        size="sm"
        footer={
          <>
            {result && (
              <Link to={`/employees/${result.employee_id}`}>
                <Button variant="primary">Open employee record</Button>
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
                { label: 'Employee code', value: result.employee_code },
                { label: 'Role granted', value: result.role },
              ]}
            />
            {result.temporary_password ? (
              <Banner tone="warning" title="Temporary password — shown only once">
                <p className="mb-2">
                  This is the only time this password is displayed. No other endpoint returns it. A
                  welcome email with sign-in instructions has also gone to the work email; the
                  employee must change this password at first sign-in.
                </p>
                <code className="block rounded-lg border border-warning/30 bg-surface px-3 py-2 font-mono text-sm text-ink">
                  {result.temporary_password}
                </code>
              </Banner>
            ) : (
              <Banner tone="info">
                No temporary password was issued; the account uses the credential flow configured by
                your administrator.
              </Banner>
            )}
          </div>
        )}
      </Modal>
    </Card>
  )
}
