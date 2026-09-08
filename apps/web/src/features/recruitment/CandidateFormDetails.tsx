/**
 * Everything the HRMS holds about a candidate as a person — the identity and
 * experience columns, and every answer their application form collected —
 * rendered ONE way, wherever it is shown.
 *
 * Used by the candidate page (Candidates → candidate) and by the application
 * page (Applications → application), so the two routes cannot drift: same
 * record, same fields, same link handling. Real http(s) links (Drive, LinkedIn,
 * a portfolio) open in a new tab; anything that is not a URL is shown exactly
 * as the candidate typed it, so HR sees what was given and can ask for better.
 */

import { Card, CardHeader, DescriptionList } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { downloadFile } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import type { Candidate } from '@/lib/types'

const URL_RE = /^https?:\/\//i

export function linkOrText(value: unknown) {
  const text = Array.isArray(value) ? value.join(', ') : String(value ?? '')
  if (!URL_RE.test(text)) return text || '—'
  return (
    <a
      href={text}
      target="_blank"
      rel="noreferrer noopener"
      className="break-all text-brand hover:underline"
    >
      {text}
    </a>
  )
}

export function CandidateFormDetails({
  candidate,
  title = 'Candidate information',
  description,
}: {
  candidate: Candidate
  title?: string
  description?: string
}) {
  const toast = useToast()
  return (
    <Card className="space-y-4">
      <CardHeader title={title} description={description} />
      {/*
        Exactly the nine questions the application form asks, in the form's
        own order — nothing internal. HR's working fields (consent, source,
        notice period, employer) live on the candidate record and its
        banners, not here: this card answers "what did the person submit?".
      */}
      <DescriptionList
        columns={2}
        items={[
          { label: 'Full Name', value: candidate.full_name || '—' },
          {
            label: 'Email',
            value: candidate.email ? (
              <a href={`mailto:${candidate.email}`} className="text-brand hover:underline">
                {candidate.email}
              </a>
            ) : (
              '—'
            ),
          },
          { label: 'Mobile Number', value: candidate.phone || '—' },
          { label: 'City', value: linkOrText(candidate.profile?.city) },
          { label: 'Education', value: linkOrText(candidate.profile?.qualification) },
          {
            label: 'Resume Upload',
            value: candidate.has_resume ? (
              <Button
                size="sm"
                className="max-w-full"
                title={candidate.resume_name || 'resume.pdf'}
                onClick={() =>
                  void downloadFile(
                    `/candidates/${candidate.id}/resume/`,
                    candidate.resume_name || 'resume.pdf',
                  ).catch(() => toast.error('Could not download the résumé'))
                }
              >
                <span className="truncate">
                  Download{candidate.resume_name ? ` — ${candidate.resume_name}` : ''}
                </span>
              </Button>
            ) : (
              '—'
            ),
          },
          { label: 'Resume Link', value: linkOrText(candidate.profile?.resume_link) },
          {
            label: 'Current Salary',
            value: candidate.profile?.current_salary != null
              ? formatCurrency(String(candidate.profile.current_salary))
              : '—',
          },
          { label: 'Expected Salary', value: formatCurrency(candidate.expected_ctc) },
        ]}
      />
    </Card>
  )
}