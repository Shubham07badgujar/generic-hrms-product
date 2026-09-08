import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import clsx from 'clsx'
import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-soft' | 'link'
type Size = 'sm' | 'md' | 'lg' | 'icon'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand text-white hover:bg-brand-hover shadow-sm',
  secondary: 'bg-surface text-ink border border-line hover:bg-canvas shadow-sm',
  ghost: 'text-ink-muted hover:bg-canvas hover:text-ink',
  danger: 'bg-danger text-white hover:bg-danger-hover shadow-sm',
  // For destructive actions that need to look serious without dominating a
  // toolbar — the confirmation dialog carries the full-weight red button.
  'danger-soft': 'bg-danger-soft text-danger-ink border border-danger/25 hover:bg-danger/15',
  link: 'text-brand hover:text-brand-hover underline-offset-4 hover:underline',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5 rounded-lg',
  md: 'h-9 px-3.5 text-sm gap-2 rounded-lg',
  lg: 'h-11 px-5 text-base gap-2 rounded-xl',
  icon: 'h-9 w-9 rounded-lg',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
  fullWidth?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    leadingIcon,
    trailingIcon,
    fullWidth,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      // A loading button stays in the DOM and stays focused; `aria-busy` tells
      // a screen reader why it is inert instead of it silently going dead.
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-colors duration-100',
        'disabled:pointer-events-none disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner className="h-3.5 w-3.5" /> : leadingIcon}
      {size !== 'icon' && children}
      {!loading && trailingIcon}
    </button>
  )
})
