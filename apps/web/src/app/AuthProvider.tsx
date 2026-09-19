/**
 * Session state.
 *
 * The bootstrap is the interesting part. On mount there is no access token,
 * because tokens are held in memory and memory does not survive a reload. So
 * the provider optimistically calls `/auth/refresh/`: if the httpOnly refresh
 * cookie is still valid the session is silently restored, and if it isn't the
 * 401 simply means "logged out".
 *
 * That is why `status` has a `booting` state distinct from `anonymous`.
 * Without it every reload would flash the login screen for the duration of one
 * round trip, and a route guard reading `anonymous` too early would redirect an
 * authenticated user away from the page they asked for.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  apiGet,
  apiPost,
  refreshAccessToken,
  setAccessToken,
  setSessionLostHandler,
} from '@/lib/api'
import { Permissions, DENY_ALL } from '@/lib/permissions'
import type { Me, PermissionSnapshot } from '@/lib/types'

type Status = 'booting' | 'authenticated' | 'anonymous'

/**
 * Which door a sign-in goes through.
 *
 * THREE DOORS, ONE PER AUDIENCE, and the backend is what makes them different:
 * each path applies its own throttle and refuses anyone who does not belong to
 * it, with a message identical to a wrong password so the response never
 * reveals which kind of account an address is. A named union rather than a
 * boolean because there are three of them now, and `login(email, pw, true,
 * false)` is how the wrong one gets used.
 */
export type Entrance = 'organization' | 'admin' | 'platform'

const ENTRANCE_PATHS: Record<Entrance, string> = {
  organization: '/auth/login/',
  admin: '/auth/login/admin/',
  platform: '/auth/login/platform/',
}

interface AuthState {
  status: Status
  user: Me | null
  permissions: Permissions
  mustChangePassword: boolean
  login: (email: string, password: string, entrance?: Entrance) => Promise<void>
  logout: () => Promise<void>
  reloadPermissions: () => Promise<void>
  /** Re-fetch /me/ so guards reading its flags (onboarding, handbook) see
   *  state the user just changed, without a full reload. */
  reloadUser: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

/**
 * The bootstrap refresh, run at most ONCE per page load.
 *
 * This guard is module-level rather than a ref because it has to survive a
 * component remount. React StrictMode mounts effects twice in development, and
 * the backend ROTATES and BLACKLISTS refresh tokens: the first call spends the
 * cookie and issues a new one, so a second call racing it presents an
 * already-blacklisted token, gets 401, and logs the user out on every reload.
 *
 * The same race is reachable in production without StrictMode — two tabs
 * waking together, or a reload while a refresh is in flight — so this is not a
 * development-only workaround.
 */
let bootstrapAttempt: Promise<void> | null = null

function bootstrapSession(): Promise<void> {
  bootstrapAttempt ??= refreshAccessToken().then(
    () => undefined,
    (error) => {
      // A failed bootstrap is not cached: the user may sign in afterwards, and
      // a later reload must be free to try the cookie again.
      bootstrapAttempt = null
      throw error
    },
  )
  return bootstrapAttempt
}

/** Called on sign-out so the next session bootstraps cleanly. */
function resetBootstrap() {
  bootstrapAttempt = null
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}

/** Convenience: the permission helper on its own, which most components want. */
export function usePermissions() {
  return useAuth().permissions
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('booting')
  const [user, setUser] = useState<Me | null>(null)
  const [snapshot, setSnapshot] = useState<PermissionSnapshot | null>(null)
  const [mustChangePassword, setMustChangePassword] = useState(false)
  const queryClient = useQueryClient()
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const loadSession = useCallback(async () => {
    // Both in one round of requests: identity and authority always change
    // together, and a UI holding one without the other is incoherent.
    const [me, permissionSnapshot] = await Promise.all([
      apiGet<Me>('/me/'),
      apiGet<PermissionSnapshot>('/me/permissions/'),
    ])
    if (!mounted.current) return
    setUser(me)
    setSnapshot(permissionSnapshot)
    setMustChangePassword(me.must_change_password)
    setStatus('authenticated')
  }, [])

  const clearSession = useCallback(() => {
    setAccessToken(null)
    setUser(null)
    setSnapshot(null)
    setMustChangePassword(false)
    setStatus('anonymous')
    resetBootstrap()
    // Drop every cached response: the next user must never see the last one's
    // rows sitting in the query cache.
    queryClient.clear()
  }, [queryClient])

  // The api layer calls this when a refresh fails mid-flight.
  useEffect(() => {
    setSessionLostHandler(() => {
      if (mounted.current) clearSession()
    })
    return () => setSessionLostHandler(null)
  }, [clearSession])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await bootstrapSession()
        if (!cancelled) await loadSession()
      } catch {
        if (!cancelled && mounted.current) {
          setAccessToken(null)
          setStatus('anonymous')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [loadSession])

  const login = useCallback(
    async (email: string, password: string, entrance: Entrance = 'organization') => {
      const result = await apiPost<{ access: string; must_change_password: boolean }>(
        ENTRANCE_PATHS[entrance],
        {
          email,
          password,
        },
      )
      setAccessToken(result.access)
      // The refresh token was set as an httpOnly cookie by this response; the
      // client never sees or stores it.
      await loadSession()
    },
    [loadSession],
  )

  const logout = useCallback(async () => {
    try {
      await apiPost('/auth/logout/')
    } catch {
      // A failed logout still ends the local session — the refresh token is
      // either already dead or will expire. Leaving the user "signed in"
      // because the server hiccuped is the worse outcome.
    } finally {
      clearSession()
    }
  }, [clearSession])

  const reloadPermissions = useCallback(async () => {
    const fresh = await apiGet<PermissionSnapshot>('/me/permissions/')
    if (mounted.current) setSnapshot(fresh)
  }, [])

  const reloadUser = useCallback(async () => {
    const me = await apiGet<Me>('/me/')
    if (!mounted.current) return
    setUser(me)
    setMustChangePassword(me.must_change_password)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      status,
      user,
      permissions: snapshot ? new Permissions(snapshot) : DENY_ALL,
      mustChangePassword,
      login,
      logout,
      reloadPermissions,
      reloadUser,
    }),
    [status, user, snapshot, mustChangePassword, login, logout, reloadPermissions, reloadUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
