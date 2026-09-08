/**
 * The application shell.
 *
 * Responsive strategy: one sidebar component, two presentations. On `lg` and
 * up it is a static column; below that the same markup renders inside an
 * overlay drawer. Duplicating it as separate desktop and mobile navigations is
 * how the two drift apart.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { useAuth, usePermissions } from './AuthProvider'
import { visibleNavigation } from './navigation'
import { NotificationBell } from '@/features/notifications/NotificationBell'
import { Avatar } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { humanize } from '@/lib/format'
import { useBranding } from '@/lib/branding'

/*
 * Display labels for the SEEDED template roles. Custom roles created at
 * runtime fall through to humanize(), so nothing here is mandatory — this
 * map only makes the starter roles read well.
 */
const ROLE_LABELS: Record<string, string> = {
  ceo: 'CEO',
  admin: 'Administrator',
  medical_director: 'Medical Director',
  operational_head: 'Operational Head',
  hr_head: 'HR Head',
  finance_head: 'Finance Head',
  senior_doctor: 'Senior Doctor',
  operations_manager: 'Operations Manager',
  hr_manager: 'HR Manager',
  accounts_manager: 'Accounts Manager',
  clinic_doctor: 'Clinic Doctor',
  cre: 'CRE',
  recruiter: 'Recruiter',
  payroll_executive: 'Payroll Executive',
  executive: 'Executive',
  therapist: 'Therapist',
  office_boy: 'Office Boy',
  employee: 'Employee',
}

export function roleLabel(code: string) {
  return ROLE_LABELS[code] ?? humanize(code)
}

//: The logo's lime — the same accent the dashboard hero and the offer
//: letterhead use. An explicit value on purpose: it must read identically
//: on the light and the dark surface, and it does.
const LIME = '#A6CE39'

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth()
  const branding = useBranding()
  const permissions = usePermissions()
  const groups = visibleNavigation(permissions)
  const location = useLocation()
  const primaryRole = permissions.roles[0]

  return (
    <div className="flex h-full flex-col">
      {/* Brand mark: the enso ring from the logo, lime over brand blue. */}
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-5">
        <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-brand">
          <span
            aria-hidden
            className="block h-4 w-4 rounded-full border-[3px]"
            style={{ borderColor: LIME }}
          />
        </span>
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-sm font-semibold tracking-tight text-ink">
            {branding.name}
          </span>
          <span className="block text-2xs font-medium uppercase tracking-widest text-ink-subtle">
            HRMS
          </span>
        </span>
      </div>

      {/* Who is signed in — every role sees their own identity here, and the
          chip mirrors the hero's lime-bordered role badges. */}
      {user && (
        <div className="mx-3 mt-3 flex items-center gap-2.5 rounded-xl bg-canvas px-3 py-2.5">
          <Avatar name={user.full_name || user.email} size="sm" />
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-sm font-medium text-ink">
              {user.full_name || user.email}
            </span>
            {primaryRole && (
              <span
                className="mt-0.5 inline-block rounded-full border px-2 py-px text-2xs font-medium text-ink-muted"
                style={{ borderColor: `${LIME}99` }}
              >
                {roleLabel(primaryRole)}
              </span>
            )}
          </span>
        </div>
      )}

      <nav
        className="scrollbar-thin flex-1 space-y-5 overflow-y-auto px-3 py-4"
        aria-label="Main navigation"
      >
        {groups.map((group) => (
          <div key={group.id} className="space-y-1">
            {group.label && (
              <p className="flex items-center gap-1.5 px-2.5 pb-1 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
                <span
                  aria-hidden
                  className="h-1 w-1 rounded-full"
                  style={{ backgroundColor: LIME }}
                />
                {group.label}
              </p>
            )}
            {group.items.map((item) => {
              const isActive = item.matchPrefix
                ? location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)
                : location.pathname === item.to
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={!item.matchPrefix}
                  onClick={onNavigate}
                  aria-current={isActive ? 'page' : undefined}
                  className={clsx(
                    'relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-all',
                    isActive
                      ? 'bg-brand-soft text-brand-ink shadow-sm'
                      : 'text-ink-muted hover:translate-x-0.5 hover:bg-canvas hover:text-ink',
                  )}
                >
                  {/* the lime "you are here" bar */}
                  {isActive && (
                    <span
                      aria-hidden
                      className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full"
                      style={{ backgroundColor: LIME }}
                    />
                  )}
                  <span className={clsx(isActive ? 'text-brand' : 'text-ink-subtle')}>
                    {item.icon}
                  </span>
                  {item.label}
                </NavLink>
              )
            })}
          </div>
        ))}
      </nav>

      {permissions.isReadOnly && (
        <div className="mx-3 mb-3 rounded-lg border border-info/25 bg-info-soft p-3">
          <p className="text-xs font-semibold text-info-ink">Read-only access</p>
          <p className="mt-0.5 text-2xs leading-relaxed text-info-ink/85">
            Your role is for oversight. Actions that change records are not available to you,
            and the server enforces this independently.
          </p>
        </div>
      )}

      <div className="border-t border-line px-5 py-3">
        <p className="text-2xs leading-relaxed text-ink-subtle">
          {branding.legal_name || branding.name}
        </p>
      </div>
    </div>
  )
}


function ThemeToggle() {
  const [dark, setDark] = useState(
    () =>
      localStorage.getItem('hrms.theme') === 'dark' ||
      (!localStorage.getItem('hrms.theme') &&
        window.matchMedia('(prefers-color-scheme: dark)').matches),
  )

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('hrms.theme', dark ? 'dark' : 'light')
  }, [dark])

  // The icon travels as `leadingIcon`: an icon-size Button deliberately drops
  // its CHILDREN, which is why this toggle rendered as an empty square.
  const icon = (
    <svg viewBox="0 0 24 24" fill="none" className="h-[18px] w-[18px]" aria-hidden>
      {dark ? (
        <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2.5v2M12 19.5v2M21.5 12h-2M4.5 12h-2M18.4 5.6 17 7M7 17l-1.4 1.4M18.4 18.4 17 17M7 7 5.6 5.6" />
        </g>
      ) : (
        <path
          d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      )}
    </svg>
  )
  return (
    <Button
      size="icon"
      variant="ghost"
      onClick={() => setDark((value) => !value)}
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
      leadingIcon={icon}
    />
  )
}

function UserMenu() {
  const { user, logout } = useAuth()
  const permissions = usePermissions()
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [open])

  if (!user) return null
  const primaryRole = permissions.roles[0]

  return (
    <div className="relative" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2.5 rounded-lg px-1.5 py-1 transition-colors hover:bg-canvas"
      >
        <Avatar name={user.full_name || user.email} size="sm" />
        <span className="hidden text-left sm:block">
          <span className="block text-sm font-medium leading-tight text-ink">
            {user.full_name || user.email}
          </span>
          {primaryRole && (
            <span className="block text-2xs leading-tight text-ink-subtle">
              {roleLabel(primaryRole)}
            </span>
          )}
        </span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-40 w-64 animate-slide-up rounded-xl border border-line bg-surface p-1.5 shadow-overlay"
        >
          <div className="space-y-1 border-b border-line px-2.5 py-2">
            <p className="truncate text-sm font-medium text-ink">{user.full_name || '—'}</p>
            <p className="truncate text-xs text-ink-muted">{user.email}</p>
            <div className="flex flex-wrap gap-1 pt-1">
              {permissions.roles.map((code) => (
                <Badge key={code} tone="brand">
                  {roleLabel(code)}
                </Badge>
              ))}
            </div>
          </div>
          <NavLink
            to="/me"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block rounded-lg px-2.5 py-2 text-sm text-ink-muted transition-colors hover:bg-canvas hover:text-ink"
          >
            My profile
          </NavLink>
          <NavLink
            to="/change-password"
            role="menuitem"
            className="block rounded-lg px-2.5 py-2 text-sm text-ink-muted transition-colors hover:bg-canvas hover:text-ink"
          >
            Change password
          </NavLink>
          <button
            type="button"
            role="menuitem"
            onClick={() => void logout()}
            className="w-full rounded-lg px-2.5 py-2 text-left text-sm text-danger-ink transition-colors hover:bg-danger-soft"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export function AppShell({ children }: { children?: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const location = useLocation()

  // Any navigation closes the drawer — otherwise it stays open over the page
  // the user just chose.
  useEffect(() => setMobileNavOpen(false), [location.pathname])

  return (
    <div className="min-h-screen bg-canvas">
      <a href="#main" className="skip-link">
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-line bg-surface lg:block">
        <SidebarContent />
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 animate-fade-in bg-ink/40"
            onClick={() => setMobileNavOpen(false)}
            aria-hidden
          />
          <div className="relative h-full w-64 animate-slide-in-right border-r border-line bg-surface">
            <SidebarContent onNavigate={() => setMobileNavOpen(false)} />
          </div>
        </div>
      )}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur sm:px-6">
          <Button
            size="icon"
            variant="ghost"
            className="lg:hidden"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            aria-expanded={mobileNavOpen}
          >
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
          </Button>

          <div className="flex-1" />

          <div className="flex items-center gap-1">
            <NotificationBell />
            <ThemeToggle />
            <div className="mx-1 h-5 w-px bg-line" aria-hidden />
            <UserMenu />
          </div>
        </header>

        <main id="main" tabIndex={-1} className="px-4 py-6 focus-visible:outline-none sm:px-6 lg:px-8">
          <div className="mx-auto max-w-[88rem] space-y-6">{children ?? <Outlet />}</div>
        </main>
      </div>
    </div>
  )
}
