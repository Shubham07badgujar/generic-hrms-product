/**
 * What the organization's plan includes, and what that does to the UI.
 *
 * Two independent questions decide whether a control renders: may this person
 * do it (`can`), and did their company buy it (`hasFeature`). They are kept
 * apart because they fail differently — a missing permission is "ask your
 * administrator", a missing feature is "your plan does not include this" — and
 * because an HR Head on a plan without payroll holds every payroll grant their
 * role carries and has no payroll to use them on.
 *
 * NEITHER IS A SECURITY BOUNDARY. The API re-resolves the permission and
 * re-checks the entitlement on every request, and answers
 * `feature_not_available` to anyone who reaches a disabled module by typing
 * the URL. What these tests cover is a user not being shown a door that opens
 * onto an upgrade prompt.
 */

import { describe, expect, it } from 'vitest'
import { visibleNavigation } from '@/app/navigation'
import { Permissions } from '@/lib/permissions'
import { ALL_FEATURES, makeSnapshot, snapshotForRole } from './helpers'
import type { FeatureCode } from '@/lib/types'

function labels(permissions: Permissions): string[] {
  return visibleNavigation(permissions).flatMap((group) =>
    group.items.map((item) => item.label),
  )
}

function hrHeadWith(features: FeatureCode[]): Permissions {
  return new Permissions({ ...snapshotForRole('hr_head'), features })
}

describe('hasFeature', () => {
  it('is absence-means-unavailable, like grants', () => {
    const permissions = new Permissions(makeSnapshot({ features: ['core', 'leave'] }))

    expect(permissions.hasFeature('leave')).toBe(true)
    expect(permissions.hasFeature('payroll')).toBe(false)
  })

  it('denies everything before a snapshot arrives', async () => {
    const { DENY_ALL } = await import('@/lib/permissions')

    for (const feature of ALL_FEATURES) {
      expect(DENY_ALL.hasFeature(feature)).toBe(false)
    }
  })
})

describe('navigation', () => {
  it('hides a module the plan does not include', () => {
    const withPayroll = labels(hrHeadWith(ALL_FEATURES))
    const withoutPayroll = labels(
      hrHeadWith(ALL_FEATURES.filter((f) => f !== 'payroll')),
    )

    // The positive control matters more than the refusal: without it, a
    // navigation that rendered nothing would pass this test.
    expect(withPayroll).toContain('Payroll')
    expect(withPayroll).toContain('Payslips')

    expect(withoutPayroll).not.toContain('Payroll')
    expect(withoutPayroll).not.toContain('Payslips')
    expect(withoutPayroll).not.toContain('Payroll settings')
    // And nothing else moved: an HR Head keeps their people pages.
    expect(withoutPayroll).toContain('Employees')
    expect(withoutPayroll).toContain('Documents')
  })

  it('drops a whole group when its module is gone', () => {
    const groups = visibleNavigation(
      hrHeadWith(ALL_FEATURES.filter((f) => f !== 'recruitment')),
    ).map((group) => group.id)

    expect(groups).not.toContain('recruitment')
    expect(groups).toContain('people')
  })

  it('separates attendance from its biometric integration', () => {
    // A plan can include attendance without the device integration, which is a
    // real difference in what was sold rather than a finer shade of the same
    // thing.
    //
    // An explicit snapshot rather than a role fixture: none of them grants
    // attendance, so borrowing one would have tested nothing -- which is what
    // the positive control below caught when this test first used `hr_head`.
    const attendanceGrants = {
      attendance: { view: 'all' as const },
      attendance_device: { view: 'all' as const },
    }
    const both = new Permissions(
      makeSnapshot({ grants: attendanceGrants, features: ALL_FEATURES }),
    )
    const withoutDevices = new Permissions(
      makeSnapshot({
        grants: attendanceGrants,
        features: ALL_FEATURES.filter((f) => f !== 'attendance_biometric'),
      }),
    )

    expect(labels(both)).toContain('Attendance')
    expect(labels(both)).toContain('eSSL Integration')

    expect(labels(withoutDevices)).toContain('Attendance')
    expect(labels(withoutDevices)).not.toContain('eSSL Integration')
  })

  it('still requires the permission, not merely the feature', () => {
    // Every module bought, no grants held: entitlement alone opens nothing.
    const entitledButUnprivileged = new Permissions(
      makeSnapshot({ features: ALL_FEATURES, grants: {} }),
    )

    const shown = labels(entitledButUnprivileged)

    expect(shown).not.toContain('Payroll')
    expect(shown).not.toContain('Employees')
    // The two pages that need no grant at all stay: the dashboard and the
    // person's own profile.
    expect(shown).toContain('Dashboard')
    expect(shown).toContain('My profile')
  })
})
