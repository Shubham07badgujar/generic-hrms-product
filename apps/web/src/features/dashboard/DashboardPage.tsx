/**
 * Dashboards, selected by the snapshot's `dashboard` key.
 *
 * The backend sets `dashboard_key` per ROLE (not per layer), and this file
 * honours that: six dashboards, chosen server-side, each assembled from
 * permission-gated panels. A panel that the user cannot back with data simply
 * is not rendered — so an Admin and a CEO see different cards even though both
 * hold organisation-wide visibility.
 *
 * A NOTE ON NUMBERS
 * -----------------
 * Two kinds of figure appear here and they are not interchangeable.
 *
 * BI metrics (`MetricStrip`) are real aggregates the server computed and
 * scoped, and each carries the server's own description of the breadth applied.
 *
 * Queue counts come from cursor-paginated lists that return no total, so a card
 * showing one page says "25+" rather than "25". Nothing extrapolates a total
 * the server never sent.
 */

import { Link } from 'react-router-dom'
import { useAuth, usePermissions } from '@/app/AuthProvider'
import { roleLabel } from '@/app/AppShell'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useApplications,
  isCurrentEmployee,
  useEmployees,
  useHrDecisionQueue,
  useInterviews,
  useJobs,
  useMyEmployeeRecord,
  useOffers,
} from '@/lib/queries'
import { Card, CardHeader, DescriptionList, Section, StatCard } from '@/components/ui/Card'
import {
  ActionCentrePanel,
  ExitsPanel,
  MetricChartPanel,
  MetricStrip,
  MyClearancePanel,
  MyPayPanel,
  OnboardingPanel,
  PanelTitle,
  PayrollPanel,
  ProbationPanel,
} from './panels'
import { useLeaveBalances } from '@/lib/leaveQueries'
import { CardSkeleton, EmptyState } from '@/components/ui/States'
import { ApplicationStatusBadge, Badge } from '@/components/ui/Badge'
import { Banner, Avatar } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { formatDate, formatDateTime } from '@/lib/format'
import type { DashboardKey } from '@/lib/types'

/* ------------------------------------------------------------ primitives */

/**
 * A count the server can actually stand behind.
 *
 * `atLeast` is true when the figure is one page of a cursor-paginated list, in
 * which case the card says "25+" rather than "25". The distinction is small on
 * screen and large in trust.
 */
function PageCount({ count, atLeast }: { count: number; atLeast: boolean }) {
  return (
    <>
      {count}
      {atLeast && <span className="text-ink-subtle">+</span>}
    </>
  )
}

function useListStat(query: { data?: { data: unknown[]; meta: { next: string | null } } }) {
  const rows = query.data?.data ?? []
  return { count: rows.length, atLeast: Boolean(query.data?.meta.next) }
}

function PanelLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="text-sm font-medium text-brand hover:underline">
      {children}
    </Link>
  )
}

/* ---------------------------------------------------------------- panels */

function HrDecisionQueuePanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.APPLICATION, ACTION.REJECT)
  const query = useHrDecisionQueue(enabled)

  if (!enabled) return null

  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="⚖️" tone="warning">Awaiting your final decision</PanelTitle>}
        description="Candidates whose department review is complete. Only you can select or reject."
        action={rows.length > 0 && <PanelLink to="/recruitment/decisions">Open queue</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="Nothing waiting" description="No candidates are at a final-decision stage." />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((application) => (
            <li key={application.id}>
              <Link
                to={`/recruitment/applications/${application.id}`}
                className="flex items-center gap-3 py-2.5 transition-colors hover:bg-canvas"
              >
                <Avatar name={application.candidate_name} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{application.candidate_name}</p>
                  <p className="truncate text-xs text-ink-muted">
                    {application.job_title} · {application.department_name}
                  </p>
                </div>
                <Badge tone="warning">Decide</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function MyInterviewsPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.INTERVIEW)
  const query = useInterviews({ status: 'scheduled', ordering: 'scheduled_at' })

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🗓️" tone="info">Upcoming interviews</PanelTitle>}
        description="Scheduled interviews you can see."
        action={rows.length > 0 && <PanelLink to="/recruitment/interviews">View all</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No scheduled interviews" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((interview) => (
            <li key={interview.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{interview.candidate_name}</p>
                <p className="truncate text-xs text-ink-muted">
                  {interview.stage_name} · {interview.interviewer_name}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="tabular text-xs text-ink">{formatDateTime(interview.scheduled_at)}</p>
                {interview.feedback_submitted ? (
                  <Badge tone="success">Feedback in</Badge>
                ) : (
                  <Badge tone="warning">Feedback due</Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function PipelinePanel({ title = 'Active pipeline' }: { title?: string }) {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.APPLICATION)
  const query = useApplications({ status: 'active' })

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🧭" tone="brand">{title}</PanelTitle>}
        description="Candidates currently moving through a workflow."
        action={<PanelLink to="/recruitment/pipeline">Open pipeline</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No active applications" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 6).map((application) => (
            <li key={application.id}>
              <Link
                to={`/recruitment/applications/${application.id}`}
                className="flex items-center gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{application.candidate_name}</p>
                  <p className="truncate text-xs text-ink-muted">{application.job_title}</p>
                </div>
                <Badge tone="info">{application.stage_name}</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function OpenRolesPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.JOB_OPENING)
  const query = useJobs({ status: 'published' })

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="📣" tone="brand">Open roles</PanelTitle>}
        description="Published job openings within your visibility."
        action={<PanelLink to="/recruitment/jobs">All jobs</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No published roles" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((job) => (
            <li key={job.id}>
              <Link to={`/recruitment/jobs/${job.id}`} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{job.title}</p>
                  <p className="truncate text-xs text-ink-muted">
                    {job.department_name} · {job.workflow_name}
                  </p>
                </div>
                <span className="tabular shrink-0 text-xs text-ink-muted">
                  {job.application_count} applied
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function MyProfilePanel() {
  const query = useMyEmployeeRecord()

  if (query.isLoading) return <CardSkeleton />
  if (query.isError || !query.data) {
    // Admin and CEO are system principals with no Employee record. That is by
    // design, not an error, so say so instead of showing a failure.
    return (
      <Card>
        <CardHeader
          title="Employment record"
          description="Your account is a system role and has no employee record."
        />
      </Card>
    )
  }

  const employee = query.data
  return (
    <Card className="space-y-4">
      <CardHeader title={<PanelTitle icon="🪪" tone="neutral">My employment</PanelTitle>} action={<PanelLink to="/me">Full profile</PanelLink>} />
      <DescriptionList
        columns={2}
        items={[
          { label: 'Employee code', value: employee.employee_code },
          { label: 'Department', value: employee.department_name ?? '—' },
          { label: 'Designation', value: employee.designation_title || '—' },
          { label: 'Reporting manager', value: employee.reporting_manager_name || '—' },
          { label: 'Joined', value: formatDate(employee.date_of_joining) },
          { label: 'Status', value: <Badge tone="success">{employee.status}</Badge> },
        ]}
      />
    </Card>
  )
}

function OffersPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.OFFER)
  const query = useOffers({})

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader title={<PanelTitle icon="✉️" tone="success">Offers</PanelTitle>} action={<PanelLink to="/recruitment/offers">All offers</PanelLink>} />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No offers yet" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((offer) => (
            <li key={offer.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{offer.candidate_name}</p>
                <p className="text-xs text-ink-muted">Joining {formatDate(offer.joining_date)}</p>
              </div>
              <ApplicationStatusBadge status={offer.status === 'accepted' ? 'offer_accepted' : 'offer_sent'} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------- stat row */

function RecruitmentStats() {
  const permissions = usePermissions()
  const applications = useApplications({ status: 'active' })
  const jobs = useJobs({ status: 'published' })
  const interviews = useInterviews({ status: 'scheduled' })
  const queue = useHrDecisionQueue(permissions.can(RESOURCE.APPLICATION, ACTION.REJECT))

  const activeStat = useListStat(applications)
  const jobStat = useListStat(jobs)
  const interviewStat = useListStat(interviews)
  const queueStat = useListStat(queue)

  const cards = [
    permissions.can(RESOURCE.APPLICATION) && {
      key: 'active',
      label: 'Active applications',
      stat: activeStat,
      tone: 'brand' as const,
      icon: '🧭',
    },
    permissions.can(RESOURCE.JOB_OPENING) && {
      key: 'jobs',
      label: 'Published roles',
      stat: jobStat,
      tone: 'neutral' as const,
      icon: '📣',
    },
    permissions.can(RESOURCE.INTERVIEW) && {
      key: 'interviews',
      label: 'Scheduled interviews',
      stat: interviewStat,
      tone: 'neutral' as const,
      icon: '🗓️',
    },
    permissions.can(RESOURCE.APPLICATION, ACTION.REJECT) && {
      key: 'queue',
      label: 'Awaiting your decision',
      stat: queueStat,
      tone: 'warning' as const,
      icon: '⚖️',
    },
  ].filter(Boolean) as Array<{
    key: string
    label: string
    stat: { count: number; atLeast: boolean }
    tone: 'brand' | 'neutral' | 'warning'
    icon: string
  }>

  if (!cards.length) return null

  // A FRAGMENT, deliberately: every mount site places this inside its own
  // stats grid, so the cards must be direct grid cells. Wrapping them in a
  // second grid crammed all of them into one cell — the clipped
  // "ACTIVE APPLICATI…" tiles.
  return (
    <>
      {cards.map((card) => (
        <StatCard
          key={card.key}
          label={card.label}
          tone={card.tone}
          icon={<span aria-hidden className="text-lg">{card.icon}</span>}
          value={<PageCount count={card.stat.count} atLeast={card.stat.atLeast} />}
          hint={card.stat.atLeast ? 'First page — more beyond' : undefined}
        />
      ))}
    </>
  )
}

function HeadcountStat() {
  const permissions = usePermissions()
  // Everyone still with us — not the "active" status, which precedes
  // confirmation and which almost nobody holds once settled in.
  const query = useEmployees({ page_size: '200' })
  const current = (query.data?.data ?? []).filter(isCurrentEmployee)
  const stat = { count: current.length, atLeast: Boolean(query.data?.meta.next) }
  if (!permissions.can(RESOURCE.EMPLOYEE)) return null

  const scope = permissions.scopeOf(RESOURCE.EMPLOYEE)
  return (
    <StatCard
      label={scope === 'all' ? 'Current employees' : 'Employees in your scope'}
      tone="brand"
      icon={<span aria-hidden className="text-lg">👥</span>}
      value={<PageCount count={stat.count} atLeast={stat.atLeast} />}
      hint={stat.atLeast ? 'First page — more beyond' : undefined}
    />
  )
}

function LeaveSnapshotPanel() {
  const { user } = useAuth()
  const employeeId = user?.employee_id ?? undefined
  const year = String(new Date().getFullYear())
  const query = useLeaveBalances(
    employeeId ? { year, employee: employeeId } : {},
  )
  if (!employeeId) return null
  const rows = query.data?.data ?? []
  if (query.isLoading) return <CardSkeleton />
  if (!rows.length) return null

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🌴" tone="success">My leave</PanelTitle>}
        description="What you can still take this year."
        action={<PanelLink to="/leave">Apply / view</PanelLink>}
      />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {rows.slice(0, 3).map((row) => (
          <div key={row.id} className="rounded-lg bg-canvas px-3 py-2.5">
            <p className="truncate text-xs text-ink-subtle">{row.leave_type_name}</p>
            <p className="tabular text-xl font-semibold text-ink">
              {row.available}
              <span className="text-xs font-normal text-ink-subtle">
                {' '}/ {Number(row.allocated) + Number(row.carried_forward)}
              </span>
            </p>
          </div>
        ))}
      </div>
    </Card>
  )
}

/**
 * One-tap doors into the day's most likely destinations, per role.
 * Pure navigation — nothing here grants anything the routes don't check.
 */
function QuickActions({ items }: { items: Array<{ to: string; icon: string; label: string }> }) {
  return (
    <div className="flex flex-wrap gap-2.5">
      {items.map((item) => (
        <Link
          key={item.to + item.label}
          to={item.to}
          className="group flex items-center gap-2 rounded-full border border-line bg-surface py-2 pl-2.5 pr-4 shadow-card transition-all hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-lg"
        >
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-full bg-brand-soft text-sm"
          >
            {item.icon}
          </span>
          <span className="text-sm font-medium text-ink group-hover:text-brand">
            {item.label}
          </span>
        </Link>
      ))}
    </div>
  )
}

const QUICK_ACTIONS: Record<DashboardKey, Array<{ to: string; icon: string; label: string }>> = {
  ceo: [
    { to: '/reports', icon: '📊', label: 'Analytics' },
    { to: '/employees', icon: '👥', label: 'Employees' },
    { to: '/recruitment/pipeline', icon: '🧭', label: 'Pipeline' },
    { to: '/payroll', icon: '💰', label: 'Payroll' },
  ],
  admin: [
    { to: '/employees', icon: '👥', label: 'Employees' },
    { to: '/organisation', icon: '🏥', label: 'Organisation' },
    { to: '/attendance/essl', icon: '🕘', label: 'eSSL devices' },
    { to: '/audit', icon: '🔍', label: 'Audit log' },
    { to: '/reports', icon: '📊', label: 'Analytics' },
  ],
  department: [
    { to: '/recruitment/decisions', icon: '⚖️', label: 'Decision queue' },
    { to: '/leave', icon: '🌴', label: 'Leave approvals' },
    { to: '/attendance', icon: '🕘', label: 'Attendance' },
    { to: '/employees', icon: '👥', label: 'Employees' },
    { to: '/recruitment/jobs', icon: '📣', label: 'Job openings' },
  ],
  manager: [
    { to: '/leave', icon: '🌴', label: 'Leave' },
    { to: '/attendance', icon: '🕘', label: 'Attendance' },
    { to: '/recruitment/interviews', icon: '🗓️', label: 'My interviews' },
    { to: '/me', icon: '🪪', label: 'My profile' },
  ],
  executive: [
    { to: '/leave', icon: '🌴', label: 'Leave' },
    { to: '/attendance', icon: '🕘', label: 'Attendance' },
    { to: '/payslips', icon: '💸', label: 'My payslips' },
    { to: '/me', icon: '🪪', label: 'My profile' },
  ],
  self: [
    { to: '/leave', icon: '🌴', label: 'Apply for leave' },
    { to: '/attendance', icon: '🕘', label: 'My attendance' },
    { to: '/payslips', icon: '💸', label: 'My payslips' },
    { to: '/me', icon: '🪪', label: 'My profile' },
  ],
}

function timeGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

/**
 * The welcome band: brand gradient, soft decorative rings, today's date and
 * the caller's roles. Committed colours (white on brand) hold in both themes.
 */
function DashboardHero({
  name,
  subtitle,
  roles,
}: {
  name: string
  subtitle: string
  roles: string[]
}) {
  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
  // The lime is the logo's own brush-circle green (#A6CE39) — the one
  // accent, spent on the date line, the enso ring and the chip borders,
  // over the committed brand-blue ground.
  return (
    <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-brand to-brand-hover p-6 text-white shadow-card sm:p-8">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-14 -top-20 h-64 w-64 rounded-full border-[10px] border-[#A6CE39]/40"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-28 right-28 h-56 w-56 rounded-full bg-[#A6CE39]/10"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute bottom-6 right-6 hidden h-16 w-16 rounded-full border-[6px] border-[#A6CE39]/70 sm:block"
      />
      <p className="text-sm font-semibold tracking-wide text-[#C4E36A]">{today}</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
        {timeGreeting()}, {name}
      </h1>
      <p className="mt-1.5 text-sm text-white/85">{subtitle}</p>
      {roles.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {roles.map((code) => (
            <span
              key={code}
              className="rounded-full border border-[#A6CE39]/50 bg-white/10 px-3 py-1 text-xs font-medium tracking-wide backdrop-blur-sm"
            >
              {roleLabel(code)}
            </span>
          ))}
        </div>
      )}
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0 h-1 bg-gradient-to-r from-[#A6CE39] via-[#A6CE39]/60 to-transparent"
      />
    </div>
  )
}

/* ----------------------------------------------------------- dashboards */

function CeoDashboard() {
  return (
    <>
      <Banner tone="info" title="Oversight view">
        You have organisation-wide visibility and export rights. Actions that change records are
        withheld from this account, and the server refuses them independently of this screen.
      </Banner>

      {/* Real aggregates, scoped and labelled by the server. `RecruitmentStats`
          is the SAME component every other dashboard mounts: its HR-decision
          card gates on CANDIDATE/REJECT, which the CEO does not hold, so it
          drops out on its own without needing a read-only variant. */}
      <MetricStrip />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <HeadcountStat />
        <RecruitmentStats />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <MetricChartPanel
          metricKey="hr.headcount_trend"
          description="Headcount at each month end."
        />
        <MetricChartPanel
          metricKey="finance.payroll_cost_trend"
          description="Cost to company per month."
        />
        <MetricChartPanel
          metricKey="recruitment.pipeline"
          description="Open applications by stage."
        />
        <MetricChartPanel
          metricKey="finance.statutory_liability"
          description="Employee and employer statutory amounts."
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <PipelinePanel title="Hiring in progress" />
        <OpenRolesPanel />
        <ExitsPanel />
        <OnboardingPanel />
      </div>

      <PayrollPanel />
    </>
  )
}

function AdminDashboard() {
  /*
   * Layout, deliberately in three registers:
   *   1. the numbers (scalar tiles) — a ten-second read of the whole system,
   *   2. the pictures (BI charts off the metric registry) — trends and shape,
   *   3. the work (operational panels) — queues that want something done.
   * The charts render from /bi/metrics/, the same registry the Analytics page
   * reads, so this dashboard never invents a number of its own.
   */
  return (
    <>
      <Banner tone="warning" title="Administrative account">
        Your override authority is an exception path, not a routine one. You cannot perform a normal
        candidate rejection — that is HR Head's alone. Overrides are recorded in a separate table and
        written to the audit log with your name, the reason and both the previous and new state.
      </Banner>

      {/* 1 — the numbers */}
      <MetricStrip limit={8} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <HeadcountStat />
        <RecruitmentStats />
      </div>

      {/* 2 — the pictures */}
      <section aria-label="Business intelligence" className="space-y-4">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-ink">
              Business intelligence
            </h2>
            <p className="text-sm text-ink-muted">
              Live from the metric registry — the same numbers the Analytics page reads.
            </p>
          </div>
          <Link to="/reports">
            <Button variant="secondary" size="sm">
              Open analytics
            </Button>
          </Link>
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          <MetricChartPanel
            metricKey="hr.headcount_trend"
            description="Headcount at each month end."
          />
          <MetricChartPanel
            metricKey="recruitment.pipeline"
            description="Candidates at each stage of every active pipeline."
          />
          <MetricChartPanel
            metricKey="finance.payroll_cost_trend"
            description="Total payroll cost by month."
          />
          <MetricChartPanel
            metricKey="recruitment.time_to_hire"
            description="Average days from application to acceptance."
          />
        </div>
      </section>

      {/* 3 — the work */}
      <section aria-label="Operations" className="space-y-4">
        <h2 className="text-lg font-semibold tracking-tight text-ink">Today&rsquo;s queues</h2>
        <div className="grid gap-5 lg:grid-cols-2">
          <ActionCentrePanel />
          <PipelinePanel />
          <OpenRolesPanel />
          <PayrollPanel />
          <ExitsPanel />
          <OnboardingPanel />
          <ProbationPanel />
          <Card>
            <CardHeader
              title="System administration"
              description="Users, roles, org structure and workflow configuration."
            />
            <div className="mt-4 flex flex-wrap gap-2">
              <Link to="/employees">
                <Button>Employees</Button>
              </Link>
              <Link to="/organisation">
                <Button>Organisation</Button>
              </Link>
              <Link to="/recruitment/workflows">
                <Button>Workflows</Button>
              </Link>
              <Link to="/documents">
                <Button>Documents</Button>
              </Link>
              <Link to="/audit">
                <Button variant="secondary">Audit log</Button>
              </Link>
              <Link to="/reports">
                <Button variant="secondary">Analytics</Button>
              </Link>
            </div>
          </Card>
        </div>
      </section>
    </>
  )
}

function DepartmentDashboard() {
  const permissions = usePermissions()
  const isHr = permissions.can(RESOURCE.APPLICATION, ACTION.REJECT)

  return (
    <>
      {!isHr && permissions.can(RESOURCE.DEPARTMENT_DECISION, ACTION.RECOMMEND) && (
        <Banner tone="info" title="Your hiring authority">
          You record the department's recommendation. Recommending rejection routes the candidate to
          HR Head for the final decision — it does not reject them, by design.
        </Banner>
      )}

      <MetricStrip />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <HeadcountStat />
        <RecruitmentStats />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <ActionCentrePanel />
        {isHr ? <HrDecisionQueuePanel /> : <PipelinePanel title="Department pipeline" />}
        <MyInterviewsPanel />
        <OpenRolesPanel />
        {isHr && <OffersPanel />}
        <ProbationPanel />
        <OnboardingPanel />
        <ExitsPanel />
        <PayrollPanel />
        <MetricChartPanel
          metricKey="hr.headcount_trend"
          description="Headcount at each month end, within your scope."
        />
        <MyClearancePanel />
        <MyProfilePanel />
      </div>
    </>
  )
}

function ManagerDashboard() {
  return (
    <>
      <MetricStrip />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <HeadcountStat />
        <RecruitmentStats />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <ActionCentrePanel />
        <MyInterviewsPanel />
        <PipelinePanel title="Team pipeline" />
        <ProbationPanel />
        <LeaveSnapshotPanel />
        <MyClearancePanel />
        <MyPayPanel />
        <MyProfilePanel />
      </div>
    </>
  )
}

function ExecutiveDashboard() {
  const permissions = usePermissions()
  return (
    <>
      <MetricStrip />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <RecruitmentStats />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <ActionCentrePanel />
        <MyInterviewsPanel />
        {permissions.can(RESOURCE.JOB_OPENING, ACTION.CREATE) && <OpenRolesPanel />}
        {permissions.can(RESOURCE.APPLICATION, ACTION.EDIT) && <PipelinePanel title="My pipeline" />}
        <PayrollPanel />
        <LeaveSnapshotPanel />
        <MyClearancePanel />
        <MyPayPanel />
        <MyProfilePanel />
      </div>
    </>
  )
}

function SelfDashboard() {
  const permissions = usePermissions()
  return (
    <>
      <div className="grid gap-5 lg:grid-cols-2">
        <ActionCentrePanel />
        <LeaveSnapshotPanel />
        <MyProfilePanel />
        <MyPayPanel />
        {permissions.can(RESOURCE.INTERVIEW) && <MyInterviewsPanel />}
        <MyClearancePanel />
        <Card>
          <CardHeader
            title={<PanelTitle icon="🧰" tone="brand">Your workspace</PanelTitle>}
            description="Profile, leave, attendance, pay and onboarding — everything that is yours."
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <Link to="/me">
              <Button>My profile</Button>
            </Link>
            <Link to="/leave">
              <Button variant="secondary">Leave</Button>
            </Link>
            <Link to="/attendance">
              <Button variant="secondary">Attendance</Button>
            </Link>
            <Link to="/payslips">
              <Button variant="secondary">My payslips</Button>
            </Link>
            <Link to="/onboarding">
              <Button variant="secondary">Onboarding</Button>
            </Link>
          </div>
        </Card>
      </div>
    </>
  )
}

const DASHBOARDS: Record<DashboardKey, () => JSX.Element> = {
  ceo: CeoDashboard,
  admin: AdminDashboard,
  department: DepartmentDashboard,
  manager: ManagerDashboard,
  executive: ExecutiveDashboard,
  self: SelfDashboard,
}

const GREETING_BY_KEY: Record<DashboardKey, string> = {
  ceo: 'Organisation overview',
  admin: 'System overview',
  department: 'Department overview',
  manager: 'Team overview',
  executive: 'Your work',
  self: 'Your workspace',
}

export function DashboardPage() {
  const { user } = useAuth()
  const permissions = usePermissions()
  const key = permissions.dashboard
  const Dashboard = DASHBOARDS[key] ?? SelfDashboard

  const firstName = user?.first_name || user?.full_name?.split(' ')[0] || 'there'
  const actions = QUICK_ACTIONS[key] ?? QUICK_ACTIONS.self

  return (
    <>
      <DashboardHero
        name={firstName}
        subtitle={GREETING_BY_KEY[key] ?? 'Your workspace'}
        roles={permissions.roles}
      />
      <Section>
        <div className="space-y-6">
          <QuickActions items={actions} />
          <Dashboard />
        </div>
      </Section>
    </>
  )
}
