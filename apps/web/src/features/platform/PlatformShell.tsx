/**
 * The console's chrome.
 *
 * A SEPARATE SHELL, not `AppShell` with the navigation filtered down. The
 * difference matters more than it looks: `AppShell` renders
 * `visibleNavigation(permissions)`, the notification bell and the employee's
 * own profile menu — all of which are HR surfaces. Reusing it and trusting a
 * filter to empty it would mean one forgotten `hasFeature` clause away from an
 * operator being offered a link into a customer's payroll. There is no filter
 * here to forget: this file has no access to the HR navigation at all.
 *
 * The three links below are the whole console. They are written out rather
 * than derived, because a console with three destinations does not need a
 * navigation system, and a hard-coded list cannot accidentally grow an HR
 * entry the way a shared one can.
 *
 * The banner is not decoration either. An operator and a customer's
 * administrator see two different applications through the same browser, and
 * the one thing worse than the console being hard to find is not knowing which
 * of the two you are looking at.
 */

import { useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { useAuth } from '@/app/AuthProvider'
import { Button } from '@/components/ui/Button'

const LINKS = [
  { to: '/platform', label: 'Overview', end: true },
  { to: '/platform/organizations', label: 'Organizations', end: false },
  { to: '/platform/plans', label: 'Plans', end: false },
]

function PlatformNav({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation()
  return (
    <nav className="flex flex-col gap-1" aria-label="Platform navigation">
      {LINKS.map((link) => {
        const active = link.end
          ? location.pathname === link.to
          : location.pathname === link.to || location.pathname.startsWith(`${link.to}/`)
        return (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            onClick={onNavigate}
            aria-current={active ? 'page' : undefined}
            className={clsx(
              'rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              active
                ? 'bg-brand-soft text-brand-ink'
                : 'text-ink-muted hover:bg-canvas hover:text-ink',
            )}
          >
            {link.label}
          </NavLink>
        )
      })}
    </nav>
  )
}

export function PlatformShell({ children }: { children?: ReactNode }) {
  const { user, logout } = useAuth()
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="min-h-screen bg-canvas">
      <a href="#main" className="skip-link">
        Skip to content
      </a>

      {/*
        Says which product this is, on every screen. An operator's session and
        a customer administrator's session look alike from the browser's side;
        this line is what makes them not look alike to the person.
      */}
      <div className="bg-ink px-4 py-1.5 text-center text-2xs font-medium uppercase tracking-[0.2em] text-white sm:px-6">
        Platform console · SaaS operations
      </div>

      <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[88rem] items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-4">
            <span className="text-sm font-semibold tracking-tight text-ink">HRMS Platform</span>
            <div className="hidden sm:flex sm:items-center sm:gap-1">
              <PlatformNav />
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {user && (
              <span className="hidden max-w-[16rem] truncate text-xs text-ink-muted md:block">
                {user.email}
              </span>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="sm:hidden"
              onClick={() => setNavOpen((open) => !open)}
              aria-expanded={navOpen}
            >
              Menu
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        </div>
        {navOpen && (
          <div className="border-t border-line px-4 py-2 sm:hidden">
            <PlatformNav onNavigate={() => setNavOpen(false)} />
          </div>
        )}
      </header>

      <main
        id="main"
        tabIndex={-1}
        className="px-4 py-6 focus-visible:outline-none sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-[88rem] space-y-6">{children ?? <Outlet />}</div>
      </main>
    </div>
  )
}
