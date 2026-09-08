/**
 * The workflow catalogue — and, for the Admin, the workflow builder.
 *
 * This page is the proof that the pipelines are configuration. It renders the
 * stages, roles, allowed decisions and transitions straight from the API — so
 * what you see here IS what the engine executes, not a diagram maintained
 * alongside it that can quietly fall out of date.
 *
 * Authoring is process-shaped: the builder asks WHO verifies, WHO conducts
 * each interview round and WHO recommends; the server generates the stage
 * graph with its invariants intact. Drafts are editable and invisible to job
 * forms; publishing freezes the structure — a process change is a new
 * workflow, not an edit to one that candidates are riding.
 */

import { useState } from 'react'
import { Card, CardHeader, PageHeader, Section } from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { Badge, DecisionBadge, STAGE_KIND_LABELS } from '@/components/ui/Badge'
import { Banner, Tabs } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { WorkflowStepper } from './WorkflowStepper'
import { useFeedbackForms, useRoles, useWorkflow, useWorkflows } from '@/lib/queries'
import { useWorkflowAuthoring, type InterviewRoundInput } from '@/lib/orgAdminQueries'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { roleLabel } from '@/app/AppShell'

const DEPARTMENT_KINDS = [
  { value: '', label: 'Any function' },
  { value: 'medical', label: 'Medical' },
  { value: 'operations', label: 'Operations' },
  { value: 'hr', label: 'HR' },
  { value: 'finance', label: 'Finance' },
]

function WorkflowBuilderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const authoring = useWorkflowAuthoring()
  const roles = useRoles()
  const forms = useFeedbackForms()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [departmentKind, setDepartmentKind] = useState('')
  const [verificationRole, setVerificationRole] = useState('hr_manager')
  const [recommendationRole, setRecommendationRole] = useState('')
  const [rounds, setRounds] = useState<InterviewRoundInput[]>([{ name: 'Round 1', role: '' }])
  const [error, setError] = useState<string | undefined>()

  const roleOptions = (roles.data ?? [])
    .filter((role) => !role.is_read_only)
    .map((role) => ({ value: role.code, label: role.name }))
  const formOptions = (forms.data?.data ?? []).map((form) => ({
    value: form.id,
    label: form.name,
  }))

  const ready = name.trim() !== '' && rounds.length > 0 && rounds.every((r) => r.role)

  const reset = () => {
    setName('')
    setDescription('')
    setDepartmentKind('')
    setVerificationRole('hr_manager')
    setRecommendationRole('')
    setRounds([{ name: 'Round 1', role: '' }])
    setError(undefined)
  }

  const submit = () => {
    setError(undefined)
    authoring.create.mutate(
      {
        name: name.trim(),
        description,
        department_kind: departmentKind,
        verification_role: verificationRole,
        interview_rounds: rounds,
        recommendation_role: recommendationRole || null,
      },
      {
        onSuccess: () => {
          toast.success(
            'Workflow created as a draft',
            'Review the generated stages, then publish to offer it to job openings.',
          )
          reset()
          onClose()
        },
        onError: (submitError) => {
          if (submitError instanceof ApiError) setError(submitError.displayMessage)
          toast.fromError(submitError, 'Could not create')
        },
      },
    )
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={authoring.create.isPending}
      size="lg"
      title="New hiring workflow"
      description="Describe the process; the stages, decisions and routing are generated from it."
      footer={
        <>
          <Button onClick={onClose} disabled={authoring.create.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={authoring.create.isPending}
            disabled={!ready}
            onClick={submit}
          >
            Create draft
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}

        <TextInput
          label="Name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="e.g. Radiologist hiring"
        />
        <TextInput
          label="Description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Function"
            value={departmentKind}
            onChange={(event) => setDepartmentKind(event.target.value)}
            options={DEPARTMENT_KINDS}
            description="Jobs in this function will be offered the workflow."
          />
          <Select
            label="HR verification by"
            value={verificationRole}
            onChange={(event) => setVerificationRole(event.target.value)}
            options={roleOptions}
          />
        </div>

        <div className="space-y-3 rounded-lg border border-line p-3.5">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-ink">Interview rounds</p>
            <Button
              size="sm"
              disabled={rounds.length >= 6}
              onClick={() =>
                setRounds((current) => [
                  ...current,
                  { name: `Round ${current.length + 1}`, role: '' },
                ])
              }
            >
              Add round
            </Button>
          </div>
          {rounds.map((round, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto]">
              <TextInput
                aria-label={`Round ${index + 1} name`}
                value={round.name}
                onChange={(event) =>
                  setRounds((current) =>
                    current.map((r, i) => (i === index ? { ...r, name: event.target.value } : r)),
                  )
                }
                placeholder={`Round ${index + 1}`}
              />
              <Select
                aria-label={`Round ${index + 1} interviewer role`}
                required
                placeholder="Conducted by…"
                value={round.role}
                onChange={(event) =>
                  setRounds((current) =>
                    current.map((r, i) => (i === index ? { ...r, role: event.target.value } : r)),
                  )
                }
                options={roleOptions}
              />
              <Select
                aria-label={`Round ${index + 1} feedback form`}
                placeholder="No feedback form"
                value={round.feedback_form ?? ''}
                onChange={(event) =>
                  setRounds((current) =>
                    current.map((r, i) =>
                      i === index ? { ...r, feedback_form: event.target.value || null } : r,
                    ),
                  )
                }
                options={formOptions}
              />
              <Button
                size="sm"
                variant="danger-soft"
                disabled={rounds.length <= 1}
                onClick={() => setRounds((current) => current.filter((_, i) => i !== index))}
              >
                Remove
              </Button>
            </div>
          ))}
          <p className="text-xs text-ink-subtle">
            An interviewer&rsquo;s &ldquo;do not proceed&rdquo; is advisory — it routes to HR
            Head&rsquo;s decision, never straight to rejection.
          </p>
        </div>

        <Select
          label="Department recommendation by"
          placeholder="Skip — go straight to HR Head"
          value={recommendationRole}
          onChange={(event) => setRecommendationRole(event.target.value)}
          options={roleOptions}
          description="Optional. A department head who recommends before HR Head decides."
        />

        <Banner tone="info">
          Every workflow ends the same way: HR Head&rsquo;s final decision, offer, onboarding, and
          the terminal Hired/Rejected stages. Those are generated for you.
        </Banner>
      </div>
    </Modal>
  )
}

function WorkflowDetail({ id }: { id: string }) {
  const workflow = useWorkflow(id)

  if (workflow.isLoading) return <LoadingBlock label="Loading workflow" />
  if (workflow.isError) return <ErrorState error={workflow.error} />
  if (!workflow.data) return <EmptyState title="Workflow not found" />

  const stages = [...workflow.data.stages].sort((a, b) => a.order - b.order)

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2.5">
              <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-soft text-base">
                🗺️
              </span>
              {workflow.data.name}
            </span>
          }
          description={workflow.data.description}
        />
        <div className="mt-4">
          <WorkflowStepper workflow={workflow.data} orientation="horizontal" />
        </div>
      </Card>

      <Card className="space-y-4">
        <CardHeader
          title={
            <span className="flex items-center gap-2.5">
              <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-soft text-base">
                🧬
              </span>
              Stages
            </span>
          }
          description="Responsible role, allowed decisions and where each decision leads."
        />
        <div className="relative space-y-3">
          {/* the thread: every stage hangs off one lime line, like the
              journey it configures */}
          <div
            aria-hidden
            className="absolute bottom-5 left-[15px] top-5 w-px"
            style={{ backgroundColor: `${LIME}66` }}
          />
          {stages.map((stage) => (
            <div
              key={stage.id}
              className="relative rounded-lg border border-line p-3.5 pl-12 transition-shadow hover:shadow-card"
            >
              <span
                aria-hidden
                className="absolute left-2 top-3.5 grid h-7 w-7 place-items-center rounded-full border-[3px] bg-surface text-xs font-semibold text-ink"
                style={{ borderColor: LIME }}
              >
                {stage.order}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-ink">{stage.name}</p>
                <Badge tone="neutral">{STAGE_KIND_LABELS[stage.kind] ?? stage.kind}</Badge>
                {stage.responsible_role_code && (
                  <Badge tone="brand">{roleLabel(stage.responsible_role_code)}</Badge>
                )}
                {stage.is_final_hr_decision && <Badge tone="warning">Final HR decision</Badge>}
                {stage.is_terminal && (
                  <Badge tone={stage.is_won ? 'success' : 'danger'}>Terminal</Badge>
                )}
                {stage.requires_interview && <Badge tone="info">Interview</Badge>}
                {stage.requires_feedback && <Badge tone="info">Feedback required</Badge>}
              </div>

              {stage.transitions.length > 0 && (
                <div className="mt-3 space-y-1.5 border-t border-line pt-3">
                  {stage.transitions.map((transition) => (
                    <div key={transition.id} className="flex flex-wrap items-center gap-2 text-xs">
                      <DecisionBadge decision={transition.on_decision} />
                      <span aria-hidden style={{ color: LIME }}>
                        →
                      </span>
                      <span className="text-ink-muted">{transition.to_stage_name}</span>
                    </div>
                  ))}
                </div>
              )}

              {stage.allowed_decisions.length === 0 && !stage.is_terminal && (
                <p className="mt-2 text-xs text-ink-subtle">
                  No decisions configured — movement happens elsewhere.
                </p>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

export function WorkflowsPage() {
  const permissions = usePermissions()
  const toast = useToast()
  const workflows = useWorkflows()
  const authoring = useWorkflowAuthoring()
  const rows = workflows.data?.data ?? []
  const [active, setActive] = useState<string | null>(null)
  const [building, setBuilding] = useState(false)

  const selected = active ?? rows[0]?.id ?? null
  const selectedRow = rows.find((row) => row.id === selected)
  const mayAuthor = permissions.can(RESOURCE.HIRING_WORKFLOW, ACTION.CREATE)
  const mayEdit = permissions.can(RESOURCE.HIRING_WORKFLOW, ACTION.EDIT)
  const mayDelete = permissions.can(RESOURCE.HIRING_WORKFLOW, ACTION.DELETE)

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span
                aria-hidden
                className="block h-4 w-4 rounded-full border-[3px]"
                style={{ borderColor: LIME }}
              />
            </span>
            Hiring workflows
          </span>
        }
        description="The pipelines, as configuration. Adding a new one is data, not code."
        actions={
          mayAuthor && (
            <Button variant="primary" onClick={() => setBuilding(true)}>
              New workflow
            </Button>
          )
        }
      />

      <Banner tone="info" title="Why this page exists">
        Every stage, role and transition below is read from the database at request time. The
        recruitment UI renders whatever these rows say — there is no per-job-title screen anywhere
        in the application, which is what lets a new pipeline ship without a frontend change.
      </Banner>

      <Section>
        {workflows.isLoading ? (
          <LoadingBlock />
        ) : workflows.isError ? (
          <ErrorState error={workflows.error} onRetry={() => void workflows.refetch()} />
        ) : rows.length === 0 ? (
          <Card>
            <EmptyState title="No workflows configured" />
          </Card>
        ) : (
          <div className="space-y-5">
            <Tabs
              active={selected ?? ''}
              onChange={setActive}
              items={rows.map((workflow) => ({
                id: workflow.id,
                label: workflow.name,
                count: workflow.stage_count,
              }))}
            />
            {selectedRow && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={selectedRow.is_published ? 'success' : 'warning'}>
                  {selectedRow.is_published ? 'Published' : 'Draft'}
                </Badge>
                {!selectedRow.is_published && mayEdit && (
                  <Button
                    size="sm"
                    variant="primary"
                    loading={authoring.publish.isPending}
                    onClick={() =>
                      authoring.publish.mutate(selectedRow.id, {
                        onSuccess: () =>
                          toast.success(
                            'Workflow published',
                            'Job openings in its function can now use it. Its structure is frozen.',
                          ),
                        onError: (e) => toast.fromError(e, 'Could not publish'),
                      })
                    }
                  >
                    Publish
                  </Button>
                )}
                {mayDelete && (
                  <Button
                    size="sm"
                    variant="danger-soft"
                    onClick={() => {
                      if (!window.confirm(`Retire the "${selectedRow.name}" workflow?`)) return
                      authoring.remove.mutate(selectedRow.id, {
                        onSuccess: () => {
                          toast.success('Workflow retired')
                          setActive(null)
                        },
                        onError: (e) => toast.fromError(e, 'Still in use by job openings'),
                      })
                    }}
                  >
                    Retire
                  </Button>
                )}
              </div>
            )}
            {selected && <WorkflowDetail id={selected} />}
          </div>
        )}
      </Section>

      <WorkflowBuilderDialog open={building} onClose={() => setBuilding(false)} />
    </>
  )
}

