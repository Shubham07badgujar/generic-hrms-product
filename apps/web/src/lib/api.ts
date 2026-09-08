/**
 * The HTTP layer.
 *
 * ACCESS TOKEN LIVES IN MEMORY ONLY.
 * Not localStorage, not sessionStorage, not a readable cookie. Anything a
 * script can read, an XSS payload can exfiltrate. The refresh token is an
 * httpOnly cookie the browser attaches automatically and JavaScript can never
 * see — so this module never handles it, only benefits from it.
 *
 * A page reload therefore starts with no access token. That is not a bug: the
 * bootstrap calls `/auth/refresh/`, and the cookie silently restores the
 * session. Signing in survives reloads without ever persisting a bearer token.
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

export const API_BASE = '/api/v1'

/* ------------------------------------------------------------------ token */

let accessToken: string | null = null
const listeners = new Set<(token: string | null) => void>()

export function setAccessToken(token: string | null) {
  accessToken = token
  listeners.forEach((fn) => fn(token))
}
export function getAccessToken() {
  return accessToken
}
export function onTokenChange(fn: (token: string | null) => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/* ------------------------------------------------------------------ error */

/** The API's uniform envelope: `{ error: { code, message, details } }`. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, string[] | string>
    request_id?: string
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  /** Field-attributed messages, ready to hand to react-hook-form. */
  readonly fieldErrors: Record<string, string>
  /**
   * The envelope's `details`, unflattened.
   *
   * `fieldErrors` joins arrays into one string, which is right for a list of
   * validation messages and wrong for structured data — a refusal that hands
   * back the file's column headers so the caller can offer a mapping needs
   * those headers as a list, not as one space-separated line.
   */
  readonly details: Record<string, unknown>
  readonly requestId?: string

  constructor(status: number, body?: ApiErrorBody, fallback = 'Something went wrong.') {
    const envelope = body?.error
    super(envelope?.message || fallback)
    this.name = 'ApiError'
    this.status = status
    this.code = envelope?.code ?? 'unknown_error'
    this.requestId = envelope?.request_id
    this.fieldErrors = {}
    this.details = (envelope?.details ?? {}) as Record<string, unknown>

    for (const [field, value] of Object.entries(envelope?.details ?? {})) {
      this.fieldErrors[field] = Array.isArray(value) ? value.join(' ') : String(value)
    }
  }

  /** 403 vs 404 matters: the API returns 404 for out-of-scope rows on purpose. */
  get isPermissionDenied() {
    return this.status === 403
  }
  get isNotFound() {
    return this.status === 404
  }
  get isValidation() {
    return this.status === 400 || this.status === 422
  }
  get isConflict() {
    return this.status === 409
  }

  /**
   * A message worth showing a user, preferring the field detail when the
   * envelope's top-level message is the generic "Validation failed."
   */
  get displayMessage() {
    const first = Object.values(this.fieldErrors)[0]
    if (this.isValidation && first) return first
    return this.message
  }
}

/* ----------------------------------------------------------------- client */

export const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  withCredentials: true, // required for the refresh cookie
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

/*
 * Refresh is single-flight. Ten queries firing at once on a dashboard would
 * otherwise each try to refresh, and since the backend rotates and blacklists
 * refresh tokens, the second rotation would invalidate the first — logging the
 * user out for being too busy. One in-flight promise, shared by every waiter.
 */
let refreshInFlight: Promise<string> | null = null
let onSessionLost: (() => void) | null = null

export function setSessionLostHandler(fn: (() => void) | null) {
  onSessionLost = fn
}

export async function refreshAccessToken(): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = axios
      .post<{ access: string }>(`${API_BASE}/auth/refresh/`, {}, { withCredentials: true })
      .then((response) => {
        setAccessToken(response.data.access)
        return response.data.access
      })
      .finally(() => {
        refreshInFlight = null
      })
  }
  return refreshInFlight
}

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retried?: boolean
  _skipRefresh?: boolean
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const config = error.config as RetriableConfig | undefined
    const status = error.response?.status

    if (status === 401 && config && !config._retried && !config._skipRefresh) {
      config._retried = true
      try {
        const token = await refreshAccessToken()
        config.headers.Authorization = `Bearer ${token}`
        return http.request(config)
      } catch {
        setAccessToken(null)
        onSessionLost?.()
      }
    }

    if (!error.response) {
      // No response at all — the network or the server is down. Distinct from
      // a rejection by the API, and worth saying so plainly.
      throw new ApiError(0, undefined, 'Cannot reach the server. Check your connection.')
    }
    throw new ApiError(error.response.status, error.response.data)
  },
)

/* ------------------------------------------------------------- convenience */

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const { data } = await http.get<T>(url, config)
  return data
}
export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig) {
  const { data } = await http.post<T>(url, body ?? {}, config)
  return data
}
export async function apiPatch<T>(url: string, body?: unknown) {
  const { data } = await http.patch<T>(url, body ?? {})
  return data
}
export async function apiPut<T>(url: string, body?: unknown) {
  const { data } = await http.put<T>(url, body ?? {})
  return data
}
export async function apiDelete<T>(url: string) {
  const { data } = await http.delete<T>(url)
  return data
}

/* ------------------------------------------------------------- pagination */

/**
 * The API pages by NUMBER, 40 records to a page, and says so: `count`, `page`
 * and `pages` ride in `meta` alongside the `next`/`previous` URLs that
 * predate them. Screens that only ever used the URLs keep working; screens
 * that want "Page 3 of 7" now can.
 */
export interface Paginated<T> {
  data: T[]
  meta: {
    next: string | null
    previous: string | null
    page_size: number
    /** Present since the move to numbered pages (40 per page). */
    count?: number
    page?: number
    pages?: number
  }
}

/** Strips the origin so a `next` URL can be re-fetched through the same client. */
export function toRelative(absoluteUrl: string): string {
  try {
    const url = new URL(absoluteUrl)
    return url.pathname.replace(API_BASE, '') + url.search
  } catch {
    return absoluteUrl
  }
}

/**
 * Download a file through the authenticated client and hand it to the browser.
 *
 * A plain <a href> would bypass the Authorization header entirely — the access
 * token lives in memory, not in a cookie — so the request would arrive
 * unauthenticated and 401. Fetching as a blob keeps the token on the request.
 */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const response = await http.get(url, { responseType: 'blob' })

  const disposition = String(response.headers['content-disposition'] ?? '')
  const match = /filename="?([^"]+)"?/.exec(disposition)
  const filename = match?.[1] ?? fallbackName

  const objectUrl = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}
