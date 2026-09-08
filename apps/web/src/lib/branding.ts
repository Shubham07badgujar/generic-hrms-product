/**
 * The organisation's public face — name, legal name, logo.
 *
 * This is the ONE source of company identity in the frontend. Nothing about
 * the company is compiled into the bundle: the login screen, the app shell,
 * the public application form and the handbook all read this hook, and the
 * values come from `GET /org/branding/`, which serves whatever the Admin has
 * entered under Organisation → Settings. Point the product at a different
 * company's database and every screen introduces itself accordingly.
 *
 * The endpoint is public (the login page needs it before anyone signs in),
 * and the response never changes within a session, so it is fetched once and
 * cached for the lifetime of the tab. Until it arrives — or if it fails —
 * the neutral fallback keeps every screen rendering.
 */
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'

export interface Branding {
  name: string
  legal_name: string
  logo: string | null
}

export const FALLBACK_BRANDING: Branding = { name: 'HRMS', legal_name: '', logo: null }

export function useBranding(): Branding {
  const query = useQuery({
    queryKey: ['org-branding'],
    queryFn: () => apiGet<Branding>('/org/branding/'),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: 1,
  })
  return query.data ?? FALLBACK_BRANDING
}
