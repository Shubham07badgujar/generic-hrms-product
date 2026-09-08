/**
 * The candidate-facing slot selection page, and the résumé upload on the
 * application form. Both anonymous, both through the anonymous axios client.
 */

import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { ApplyPage, anonymousClient } from '@/features/recruitment/ApplyPage'
import { SlotPage } from '@/features/recruitment/SlotPage'
import { ToastProvider } from '@/components/ui/Toast'

function renderAt(path: string, element: JSX.Element, routePath: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <Routes>
            <Route path={routePath} element={element} />
          </Routes>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const future = (days: number, hour: number) => {
  const d = new Date()
  d.setDate(d.getDate() + days)
  d.setHours(hour, 0, 0, 0)
  return d.toISOString()
}

const INVITE = {
  job_title: 'Customer Relationship Executive',
  round_name: 'Round 1',
  candidate_name: 'Shubham Pramod Badgujar',
  status: 'pending',
  open: true,
  options: [
    { start: future(1, 10), end: future(1, 12) },
    { start: future(1, 14), end: future(1, 16) },
  ],
  selected: null,
}

const POSTING = {
  title: 'CRE', department: 'Human Resources', location: '', employment_type: 'full_time',
  description: '', requirements: '', accepts_applications: true,
  consent_text: 'I confirm the information provided is accurate.',
  fields: [
    { key: 'full_name', label: 'Full Name', type: 'text', target: 'candidate', required: true, options: [], help_text: '' },
    { key: 'email', label: 'Email', type: 'email', target: 'candidate', required: true, options: [], help_text: '' },
    { key: 'phone', label: 'Mobile Number', type: 'phone', target: 'candidate', required: true, options: [], help_text: '' },
    { key: 'resume', label: 'Resume Upload', type: 'file', target: 'candidate', required: false, options: [], help_text: 'PDF or Word, up to 5 MB' },
  ],
}

describe('the slot selection page', () => {
  it('offers only the offered slots and posts the chosen one', async () => {
    const requests: Array<{ method?: string; url?: string; data?: string }> = []
    anonymousClient.defaults.adapter = (async (config: { method?: string; url?: string; data?: string }) => {
      requests.push(config)
      if (config.method === 'get') return { data: INVITE, status: 200, statusText: 'OK', headers: {}, config }
      return { data: { status: 'selected', selected: INVITE.options[1] }, status: 200, statusText: 'OK', headers: {}, config }
    }) as never

    const user = userEvent.setup()
    renderAt('/interview-slot/tok9', <SlotPage />, '/interview-slot/:token')

    expect(await screen.findByText(/choose the time that suits you best/i)).toBeInTheDocument()
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(2)

    // Nothing posts until a slot is chosen.
    await user.click(screen.getByRole('button', { name: /confirm my choice/i }))
    expect(await screen.findByText(/please choose one of the times/i)).toBeInTheDocument()
    expect(requests.filter((r) => r.method === 'post')).toHaveLength(0)

    await user.click(radios[1]!)
    await user.click(screen.getByRole('button', { name: /confirm my choice/i }))
    expect(await screen.findByText(/Time received/)).toBeInTheDocument()
    const post = requests.find((r) => r.method === 'post')!
    expect(JSON.parse(post.data!)).toEqual(INVITE.options[1])
  })

  it('tells a candidate whose choice is already in that HR will confirm', async () => {
    anonymousClient.defaults.adapter = (async (config: { method?: string }) => ({
      data: { ...INVITE, status: 'selected', open: false, selected: INVITE.options[0] },
      status: 200, statusText: 'OK', headers: {}, config,
    })) as never
    renderAt('/interview-slot/tok9', <SlotPage />, '/interview-slot/:token')
    expect(await screen.findByText(/A time has already been chosen/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /confirm my choice/i })).not.toBeInTheDocument()
  })
})

describe('the application form résumé upload', () => {
  it('sends multipart with the file and the answers', async () => {
    const requests: Array<{ method?: string; data?: unknown }> = []
    anonymousClient.defaults.adapter = (async (config: { method?: string; url?: string; data?: unknown }) => {
      requests.push(config)
      if (config.method === 'get') return { data: POSTING, status: 200, statusText: 'OK', headers: {}, config }
      return { data: { reference: 'APP-XYZ', job_title: 'CRE' }, status: 201, statusText: 'Created', headers: {}, config }
    }) as never

    const user = userEvent.setup()
    renderAt('/apply/tok1', <ApplyPage />, '/apply/:token')
    await screen.findByRole('heading', { name: 'CRE' })

    await user.type(screen.getByLabelText(/full name/i), 'Shubham Badgujar')
    await user.type(screen.getByLabelText(/^email/i), 'shubham@example.test')
    await user.type(screen.getByLabelText(/mobile number/i), '9511974562')
    const file = new File(['%PDF-1.4 test'], 'Shubham Resume.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText(/resume upload/i), file)
    expect(screen.getByText('Shubham Resume.pdf')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: /submit application/i }))

    await waitFor(() => expect(requests.some((r) => r.method === 'post')).toBe(true))
    const post = requests.find((r) => r.method === 'post')!
    expect(post.data).toBeInstanceOf(FormData)
    const form = post.data as FormData
    expect((form.get('resume') as File).name).toBe('Shubham Resume.pdf')
    expect(JSON.parse(form.get('answers') as string)).toMatchObject({ full_name: 'Shubham Badgujar' })
    expect(form.get('consent')).toBe('true')
    expect(await screen.findByTestId('reference')).toHaveTextContent('APP-XYZ')
  })

  it('refuses an oversized file client-side', async () => {
    anonymousClient.defaults.adapter = (async (config: { method?: string }) => ({
      data: POSTING, status: 200, statusText: 'OK', headers: {}, config,
    })) as never
    const user = userEvent.setup()
    renderAt('/apply/tok1', <ApplyPage />, '/apply/:token')
    await screen.findByRole('heading', { name: 'CRE' })
    const big = new File([new ArrayBuffer(6 * 1024 * 1024)], 'huge.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText(/resume upload/i), big)
    expect(await screen.findByText(/larger than 5 MB/)).toBeInTheDocument()
  })
})
