import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from './app/AuthProvider'
import { ToastProvider } from './components/ui/Toast'
import { AppRoutes } from './app/router'
import { ApiError } from './lib/api'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Retrying a 403 or 404 is pointless — the answer will not change, and
        // three attempts turns one clean "not permitted" into a slow one.
        if (error instanceof ApiError) {
          if (error.status >= 400 && error.status < 500) return false
        }
        return failureCount < 2
      },
    },
    mutations: { retry: false },
  },
})

// Restore the theme before first paint so the app never flashes light then dark.
const storedTheme = localStorage.getItem('hrms.theme')
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
document.documentElement.classList.toggle('dark', storedTheme === 'dark' || (!storedTheme && prefersDark))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
