/**
 * Toast feedback.
 *
 * The live region is `polite` for success and `assertive` for errors, so a
 * failed action interrupts a screen reader and a successful one waits its turn
 * — which matches how much the user needs to know right now.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import clsx from 'clsx'
import { ApiError } from '@/lib/api'

export type ToastTone = 'success' | 'error' | 'info' | 'warning'

interface Toast {
  id: number
  tone: ToastTone
  title: string
  description?: string
}

interface ToastApi {
  push: (toast: Omit<Toast, 'id'>) => void
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
  /** Turns any thrown value into a sensible toast. */
  fromError: (error: unknown, fallbackTitle?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}

const TONE_STYLES: Record<ToastTone, string> = {
  success: 'border-success/30 bg-success-soft text-success-ink',
  error: 'border-danger/30 bg-danger-soft text-danger-ink',
  info: 'border-info/30 bg-info-soft text-info-ink',
  warning: 'border-warning/30 bg-warning-soft text-warning-ink',
}

const ICONS: Record<ToastTone, ReactNode> = {
  success: <path d="m5 13 4 4L19 7" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
  error: <path d="M12 8v5m0 4h.01" strokeWidth="2" strokeLinecap="round" />,
  info: <path d="M12 16v-5m0-4h.01" strokeWidth="2" strokeLinecap="round" />,
  warning: <path d="M12 9v4m0 4h.01" strokeWidth="2" strokeLinecap="round" />,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = nextId.current++
      setToasts((current) => [...current, { ...toast, id }])
      // Errors linger; successes get out of the way.
      window.setTimeout(() => dismiss(id), toast.tone === 'error' ? 8000 : 4500)
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, description) => push({ tone: 'success', title, description }),
      error: (title, description) => push({ tone: 'error', title, description }),
      info: (title, description) => push({ tone: 'info', title, description }),
      warning: (title, description) => push({ tone: 'warning', title, description }),
      fromError: (error, fallbackTitle = 'Action failed') => {
        if (error instanceof ApiError) {
          // A 403 is a permission answer, not a bug; say so in those terms.
          const title = error.isPermissionDenied ? 'Not permitted' : fallbackTitle
          push({ tone: 'error', title, description: error.displayMessage })
          return
        }
        push({
          tone: 'error',
          title: fallbackTitle,
          description: error instanceof Error ? error.message : undefined,
        })
      },
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.tone === 'error' ? 'alert' : 'status'}
            className={clsx(
              'pointer-events-auto flex animate-slide-up items-start gap-3 rounded-xl border p-3.5 shadow-raised',
              TONE_STYLES[toast.tone],
            )}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" className="mt-0.5 h-4 w-4 shrink-0" aria-hidden>
              {ICONS[toast.tone]}
            </svg>
            <div className="min-w-0 flex-1 space-y-0.5">
              <p className="text-sm font-medium">{toast.title}</p>
              {toast.description && (
                <p className="text-xs opacity-90">{toast.description}</p>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              className="rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
              aria-label="Dismiss notification"
            >
              <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5" aria-hidden>
                <path d="m6 6 12 12M18 6 6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
