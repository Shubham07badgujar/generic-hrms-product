/**
 * The permission model and the navigation it generates.
 *
 * These assert the CLIENT mirrors the server's rules — that the UI does not
 * offer an action the API will refuse. They are not security tests; the
 * backend's own suite proves a hand-built request from the wrong role gets 403
 * regardless of anything here.
 */

import { describe, expect, it } from 'vitest'
import { ACTION, DENY_ALL, RESOURCE } from '@/lib/permissions'
import { visibleNavigation } from '@/app/navigation'
import { permissionsForRole, permissionsFor } from './helpers'

describe('Permissions', () => {
  it('treats an absent grant as denial', () => {
    const permissions = permissionsFor({ employee: { view: 'self' } })
    expect(permissions.can(RESOURCE.EMPLOYEE)).toBe(true)
    expect(permissions.can(RESOURCE.EMPLOYEE, ACTION.DELETE)).toBe(false)
    expect(permissions.can(RESOURCE.PAYROLL_RUN)).toBe(false)
  })

  it('denies everything before a session exists', () => {
    expect(DENY_ALL.can(RESOURCE.EMPLOYEE)).toBe(false)
    expect(DENY_ALL.can(RESOURCE.APPLICATION, ACTION.REJECT)).toBe(false)
    expect(DENY_ALL.isReadOnly).toBe(true)
  })

  it('orders scopes the same way the backend does', () => {
    const permissions = permissionsFor({ employee: { view: 'department' } })
    expect(permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.VIEW, 'self')).toBe(true)
    expect(permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.VIEW, 'department')).toBe(true)
    expect(permissions.canAtLeast(RESOURCE.EMPLOYEE, ACTION.VIEW, 'all')).toBe(false)
  })
})

describe('The rejection authority', () => {
  it('is held by HR Head alone', () => {
    expect(permissionsForRole('hr_head').can(RESOURCE.APPLICATION, ACTION.REJECT)).toBe(true)

    for (const role of ['medical_director', 'recruiter', 'clinic_doctor', 'admin', 'ceo'] as const) {
      expect(
        permissionsForRole(role).can(RESOURCE.APPLICATION, ACTION.REJECT),
        `${role} must not hold APPLICATION/REJECT`,
      ).toBe(false)
    }
  })

  it('is distinct from the administrative override', () => {
    const admin = permissionsForRole('admin')
    const hrHead = permissionsForRole('hr_head')

    // Admin overrides but cannot reject; HR Head rejects but cannot override.
    expect(admin.can(RESOURCE.APPLICATION, ACTION.OVERRIDE)).toBe(true)
    expect(admin.can(RESOURCE.APPLICATION, ACTION.REJECT)).toBe(false)
    expect(hrHead.can(RESOURCE.APPLICATION, ACTION.REJECT)).toBe(true)
    expect(hrHead.can(RESOURCE.APPLICATION, ACTION.OVERRIDE)).toBe(false)
  })

  it('leaves department heads with recommendation only', () => {
    const director = permissionsForRole('medical_director')
    expect(director.can(RESOURCE.DEPARTMENT_DECISION, ACTION.RECOMMEND)).toBe(true)
    expect(director.can(RESOURCE.APPLICATION, ACTION.REJECT)).toBe(false)
    expect(director.can(RESOURCE.APPLICATION, ACTION.APPROVE)).toBe(false)
  })
})

describe('Segregation of duties', () => {
  it('keeps a recruiter out of employee creation', () => {
    const recruiter = permissionsForRole('recruiter')
    expect(recruiter.can(RESOURCE.APPLICATION, ACTION.EDIT)).toBe(true)
    expect(recruiter.can(RESOURCE.EMPLOYEE, ACTION.CREATE)).toBe(false)
    expect(recruiter.can(RESOURCE.OFFER, ACTION.CREATE)).toBe(false)
  })

  it('gives an interviewer self-scope only', () => {
    const doctor = permissionsForRole('clinic_doctor')
    expect(doctor.scopeOf(RESOURCE.INTERVIEW)).toBe('self')
    expect(doctor.scopeOf(RESOURCE.APPLICATION)).toBe('self')
    expect(doctor.can(RESOURCE.INTERVIEW_FEEDBACK, ACTION.CREATE)).toBe(true)
  })

  it('gives a department head department-scope, never organisation-wide', () => {
    const director = permissionsForRole('medical_director')
    expect(director.scopeOf(RESOURCE.APPLICATION)).toBe('department')
    expect(director.canAtLeast(RESOURCE.APPLICATION, ACTION.VIEW, 'all')).toBe(false)
  })
})

describe('The CEO', () => {
  it('is read-only and holds no write action anywhere', () => {
    const ceo = permissionsForRole('ceo')
    expect(ceo.isReadOnly).toBe(true)

    const writeActions = [
      ACTION.CREATE, ACTION.EDIT, ACTION.DELETE, ACTION.APPROVE,
      ACTION.REJECT, ACTION.OVERRIDE, ACTION.RECOMMEND, ACTION.DECIDE,
    ]
    for (const resource of Object.values(RESOURCE)) {
      for (const action of writeActions) {
        expect(ceo.can(resource, action), `CEO must not hold ${resource}/${action}`).toBe(false)
      }
    }
  })

  it('keeps view and export', () => {
    const ceo = permissionsForRole('ceo')
    expect(ceo.can(RESOURCE.EMPLOYEE, ACTION.VIEW)).toBe(true)
    expect(ceo.can(RESOURCE.EMPLOYEE, ACTION.EXPORT)).toBe(true)
  })
})

describe('Navigation', () => {
  it('differs by role rather than being one list for everyone', () => {
    const labels = (role: Parameters<typeof permissionsForRole>[0]) =>
      visibleNavigation(permissionsForRole(role)).flatMap((group) =>
        group.items.map((item) => item.label),
      )

    const hrHead = labels('hr_head')
    const doctor = labels('clinic_doctor')
    const employee = labels('employee')

    expect(hrHead).not.toEqual(doctor)
    expect(doctor).not.toEqual(employee)
    // The dashboard and own profile are the only universal entries.
    expect(employee).toContain('Dashboard')
    expect(employee).toContain('My profile')
  })

  it('shows the decision queue only to the role that can work it', () => {
    const hasQueue = (role: Parameters<typeof permissionsForRole>[0]) =>
      visibleNavigation(permissionsForRole(role))
        .flatMap((group) => group.items)
        .some((item) => item.label === 'Decision queue')

    expect(hasQueue('hr_head')).toBe(true)
    expect(hasQueue('medical_director')).toBe(false)
    expect(hasQueue('recruiter')).toBe(false)
    expect(hasQueue('admin')).toBe(false)
  })

  it('hides recruitment entirely from someone with no recruitment grants', () => {
    const groups = visibleNavigation(permissionsForRole('employee'))
    expect(groups.some((group) => group.id === 'recruitment')).toBe(false)
  })

  it('drops empty groups rather than rendering an empty heading', () => {
    const groups = visibleNavigation(permissionsForRole('clinic_doctor'))
    for (const group of groups) {
      expect(group.items.length).toBeGreaterThan(0)
    }
  })

  it('gives an anonymous principal nothing but the dashboard', () => {
    const groups = visibleNavigation(DENY_ALL)
    const labels = groups.flatMap((group) => group.items.map((item) => item.label))
    expect(labels).toEqual(['Dashboard', 'My profile'])
  })
})
