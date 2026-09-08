/**
 * The mandatory-reason dialog and the dialog accessibility contract.
 *
 * The 20-character floor is enforced in three places — a database
 * CheckConstraint, the serializer, and here. This suite covers the third, and
 * exists so the user is told before submitting rather than after. It does not
 * make the other two optional.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MIN_REASON_LENGTH, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Modal } from '@/components/ui/Modal'
import { renderWithProviders } from './helpers'

describe('ReasonDialog', () => {
  it('blocks submission until the reason is long enough', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()

    renderWithProviders(
      <ReasonDialog
        open
        onClose={() => {}}
        onSubmit={onSubmit}
        title="Reject candidate"
        confirmLabel="Reject candidate"
      />,
    )

    const confirm = screen.getByRole('button', { name: 'Reject candidate' })
    expect(confirm).toBeDisabled()

    await user.type(screen.getByLabelText(/Reason/), 'too short')
    expect(confirm).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('submits the trimmed reason once it meets the floor', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const reason = 'Does not meet the clinical requirements for this role.'

    renderWithProviders(
      <ReasonDialog
        open
        onClose={() => {}}
        onSubmit={onSubmit}
        title="Reject candidate"
        confirmLabel="Reject candidate"
      />,
    )

    await user.type(screen.getByLabelText(/Reason/), `  ${reason}  `)
    const confirm = screen.getByRole('button', { name: 'Reject candidate' })
    await waitFor(() => expect(confirm).toBeEnabled())

    await user.click(confirm)
    expect(onSubmit).toHaveBeenCalledWith(reason)
  })

  it('counts against the same floor the API uses', () => {
    // Guards against the client drifting from the server's MIN_REASON_LENGTH.
    expect(MIN_REASON_LENGTH).toBe(20)
  })

  it('shows the character counter so the requirement is visible', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <ReasonDialog open onClose={() => {}} onSubmit={() => {}} title="Reject candidate" />,
    )

    expect(screen.getByText(`0/${MIN_REASON_LENGTH}`)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/Reason/), 'abc')
    expect(screen.getByText(`3/${MIN_REASON_LENGTH}`)).toBeInTheDocument()
  })

  it('surfaces a server-side rejection of the reason', () => {
    renderWithProviders(
      <ReasonDialog
        open
        onClose={() => {}}
        onSubmit={() => {}}
        title="Reject candidate"
        serverError="A reason of at least 20 characters is required."
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('at least 20 characters')
  })
})

describe('Dialog accessibility', () => {
  it('announces itself as a modal labelled by its heading', () => {
    renderWithProviders(
      <Modal open onClose={() => {}} title="Reject candidate" description="Asha Candidate">
        <p>Body</p>
      </Modal>,
    )

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('Reject candidate')
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    renderWithProviders(
      <Modal open onClose={onClose} title="Confirm">
        <p>Body</p>
      </Modal>,
    )

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('refuses to close while a mutation is in flight', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    renderWithProviders(
      <Modal open busy onClose={onClose} title="Saving">
        <p>Body</p>
      </Modal>,
    )

    await user.keyboard('{Escape}')
    // Otherwise a stray Escape mid-save discards what the user typed while the
    // request is already on its way.
    expect(onClose).not.toHaveBeenCalled()
  })

  it('traps Tab inside the dialog', async () => {
    const user = userEvent.setup()

    renderWithProviders(
      <>
        <button type="button">Outside</button>
        <Modal
          open
          onClose={() => {}}
          title="Trapped"
          footer={<button type="button">Confirm</button>}
        >
          <button type="button">Inside</button>
        </Modal>
      </>,
    )

    const dialog = screen.getByRole('dialog')
    for (let index = 0; index < 6; index += 1) {
      await user.tab()
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    }
  })
})
