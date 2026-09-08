/**
 * Modal and Drawer.
 *
 * Both are built on the same overlay, which does the four things an accessible
 * dialog must do and that a bare `<div>` never does:
 *
 *   1. traps Tab inside the dialog while it is open
 *   2. restores focus to whatever opened it on close
 *   3. closes on Escape
 *   4. marks the dialog with role/aria-modal and points aria-labelledby at the
 *      real heading, so a screen reader announces what just opened
 *
 * It also locks body scroll, because a dialog whose background scrolls under a
 * touch drag feels broken.
 */

import { useCallback, useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx'
import { Button } from './Button'

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

function useDialogBehaviour(open: boolean, onClose: () => void) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  // The latest close handler, readable from the effect below without being a
  // dependency of it. Callers pass inline closures, so `onClose` has a new
  // identity on every render of the owner — and when the owner holds the
  // dialog's input state (a controlled textarea in the page component), it
  // re-renders on every keystroke. With `onClose` in the dependency list the
  // effect tore down and re-ran per keystroke, and its setup refocuses the
  // first control in the panel — the header's close button — which stole
  // focus from the field mid-word. One character per click, forever.
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    if (!open) return

    restoreTo.current = document.activeElement as HTMLElement | null
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    // Focus the first control, or the panel itself if there is none.
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panel)?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !panel) return

      const targets = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null,
      )
      if (!targets.length) {
        event.preventDefault()
        return
      }
      const first = targets[0]!
      const last = targets[targets.length - 1]!

      // Wrap at both ends — this is the trap.
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = overflow
      restoreTo.current?.focus?.()
    }
    // `open` alone: setup (focus capture, focus steal, scroll lock) must run
    // once per opening, never per render of the owner.
  }, [open])

  return panelRef
}

interface BaseDialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  /** Set while a mutation is in flight so a click-away cannot discard input. */
  busy?: boolean
}

const WIDTHS = {
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-3xl',
  xl: 'max-w-5xl',
} as const

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  busy,
  size = 'md',
}: BaseDialogProps & { size?: keyof typeof WIDTHS }) {
  const titleId = useId()
  const descriptionId = useId()
  const guardedClose = useCallback(() => {
    if (!busy) onClose()
  }, [busy, onClose])
  const panelRef = useDialogBehaviour(open, guardedClose)

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div
        className="absolute inset-0 animate-fade-in bg-ink/40 backdrop-blur-[2px]"
        onClick={guardedClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={clsx(
          'relative flex max-h-[92vh] w-full flex-col animate-slide-up',
          'rounded-t-2xl border border-line bg-surface shadow-overlay sm:rounded-2xl',
          WIDTHS[size],
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight text-ink">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="text-sm text-ink-muted">
                {description}
              </p>
            )}
          </div>
          <Button size="icon" variant="ghost" onClick={guardedClose} aria-label="Close dialog">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" aria-hidden>
              <path
                d="m6 6 12 12M18 6 6 18"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </Button>
        </div>

        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-canvas/60 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

/**
 * A right-hand drawer.
 *
 * Used where the surrounding list is context worth keeping visible — reviewing
 * an interview while the schedule stays on screen — and a modal would hide it.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  busy,
  width = 'md',
}: BaseDialogProps & { width?: 'sm' | 'md' | 'lg' }) {
  const titleId = useId()
  const guardedClose = useCallback(() => {
    if (!busy) onClose()
  }, [busy, onClose])
  const panelRef = useDialogBehaviour(open, guardedClose)

  if (!open) return null

  const widths = { sm: 'sm:max-w-md', md: 'sm:max-w-xl', lg: 'sm:max-w-3xl' }

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 animate-fade-in bg-ink/40 backdrop-blur-[2px]"
        onClick={guardedClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={clsx(
          'relative flex h-full w-full flex-col animate-slide-in-right',
          'border-l border-line bg-surface shadow-overlay',
          widths[width],
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight text-ink">
              {title}
            </h2>
            {description && <p className="text-sm text-ink-muted">{description}</p>}
          </div>
          <Button size="icon" variant="ghost" onClick={guardedClose} aria-label="Close panel">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" aria-hidden>
              <path
                d="m6 6 12 12M18 6 6 18"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </Button>
        </div>

        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-canvas/60 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
