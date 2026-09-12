# Messaging Isolation

How the product decides **who** hears about something, **as whom** the message
goes out, and **whose wording** it uses.

This is a companion to `PLATFORM_ADMINISTRATION.md` (which covers the two
security domains) and to `CONFIGURATION.md` (which covers where settings live).
It exists because messaging is the one part of the system that deliberately
reaches *outside* the database, so a scoping mistake here is not a wrong list
on a screen — it is a message in somebody else's inbox, and it cannot be
recalled.

---

## 0. The rule

> **No message produced by one organization's action may be addressed to
> another organization's people.**

Everything below implements that sentence. It applies to in-app notifications,
to notification email, and to candidate correspondence alike.

---

## 1. The defect this design exists to prevent

The audience for "somebody must approve this payroll run" was resolved by
reading **every active user on the platform** and keeping whichever ones held
`PAYROLL_RUN / APPROVE`:

```python
# the old shape
return [
    user
    for user in User.objects.filter(is_active=True)
    if can(user, resource, action) >= minimum
]
```

Resolving the audience from the permission matrix rather than from a hardcoded
role list is right, and that part is unchanged. What was wrong is the set it
was drawn from. Holding `PAYROLL_RUN / APPROVE` **in one's own company** is
exactly what every customer's finance lead holds — so one company processing
payroll addressed every other company's approvers, with its employee count and
net pay in the body, and sent them email.

Eleven call sites fanned out this way. Nine are events in `events.py` —
department decisions, document verification, probation reviews, resignations,
three payroll events, statutory verification and administrative overrides. The
other two are in services that import the helper: the uninformed-absence flag
in `apps/leave/services.py` and the interview-slot notice in
`apps/recruitment/services/slots.py`.

Those last two are worth dwelling on. The first version of the guard below
scanned only the module where the helper is **defined**, so it reported full
coverage while saying nothing about either caller. A guard that inspects a
definition and not its callers misses the interesting half, and it did.

Note what did *not* catch it. The tenant manager stamped those notification
rows with the acting organization, so the recipient could not **read** them in
the app — the row was filtered out of their list. The leak was entirely in the
title, the body and the email, which is why a test asserting "the other
organization sees nothing" would have passed while the mail was going out.

---

## 2. Two layers, and why both

### Layer one — the audience is drawn from one organization

`_users_holding()` takes the organization as its **first positional argument**,
with no default:

```python
def _users_holding(organization, resource, action, *, scope_at_least=None):
    return [
        user
        for user in member_users(organization)
        if can(user, resource, action) >= minimum
    ]
```

A keyword argument defaulting to `None` would have kept every existing call
site compiling and left the defect in place. A required positional means a new
event cannot be written without answering *whose*, and `None` addresses
**nobody** rather than everybody.

Each of the eleven call sites takes the organization from the record it
already holds — the payroll run, the resignation, the document, the employee,
the slot invite. One exception:
`statutory_verification_due` is passed one explicitly, because
`StatutoryRuleSet` is deliberately global (India's PF and ESI rates are a fact
about the Republic, not about a customer), so the rate sets name nobody to
tell.

### Layer two — `notify()` refuses a recipient who is not a member

Every event resolves its own audience, and several build a recipient list by
hand from a reporting manager or a job opening's recruiter. Rather than
trusting two dozen functions to filter correctly, the addressing is checked
once, on the way to the row:

| Condition | Outcome |
|---|---|
| No organization bound | No row, no send, logged at ERROR |
| Recipient is not an active member of the bound organization | No row, no send, logged at ERROR |
| Otherwise | Written and delivered |

Both refusals are logged at ERROR rather than absorbed, because each one is
either a bug in an event function or a caller that forgot to bind context, and
both want finding.

The unbound case matters as much as the cross-tenant one. It is the Celery
shape: a task that did not bind its tenant cannot show that a recipient is in
scope, and writing the row anyway would stamp it from whatever the worker last
did.

---

## 3. Sender identity

```
Organization A → SMTP A → templates A → reply-to A → delivery log A
Organization B → SMTP B → templates B → reply-to B → delivery log B
```

Every send resolves its connection, envelope sender and reply-to through
`core.config.email_config(organization)`, which falls back **field by field to
the deployment's settings and never to another organization**. An organization
that sets only its own from-address, leaving the mail server to the platform,
gets exactly that.

Three send paths, and where each gets its organization:

| Path | Organization comes from |
|---|---|
| Account/welcome mail (`accounts`) | The recipient's own membership |
| Candidate correspondence (`recruitment`) | The `CandidateNotification` row |
| Notification email (`notifications`) | The `Notification` row |

**From the row, not from ambient context.** That is what makes a send that
happens later — a retry, a queued batch, a worker — still leave as the right
company. The candidate path used to read the acting organization, which meant
a retry with nothing bound named no company at all.

### Platform-originated mail

A brand-new organization has no mail settings, so the invitation announcing its
existence cannot be sent as it. `email_config(None)` resolves to the
deployment's own server, which is the platform's. This is the one place the two
security domains legitimately share a mechanism, and it is stated in code
rather than left to fall out of a default.

---

## 4. Wording

Shipped templates stay where they are, under `apps/*/templates/`, and remain
the default. An organization may **override** one message with a row in
`OrgEmailTemplate`, keyed by the shipped template's own path
(`recruitment/email/offer_sent`) so there is no second registry of message
names to keep in step.

```
render_message(organization, key, context)
    → the organization's own row, if it has one
    → the shipped template, otherwise
    → never another organization's wording
```

Seeding every shipped template into every new tenant as rows was rejected: it
would copy 51 files into the database per customer at provisioning, and a
wording fix in the product would then reach nobody, because every organization
would be sitting on a private copy made on the day they signed up.

The render context is built by the sending service and holds plain strings —
names, dates, a formatted amount — so an organization authoring a template
reaches its own message's facts and nothing behind them. Subjects are
whitespace-collapsed on both paths, because a newline in a subject is a
header-injection attempt once it reaches SMTP and an organization's own subject
box is not reviewed the way the shipped templates are.

---

## 5. Delivery records

`NotificationDelivery` declares `org_source = "notification"`, so its
organization is **derived from the notification it delivers** rather than from
acting context. A row that disagrees with its parent is refused at save time.
That is what stops one customer's send appearing in another's log, and it means
a retry running on a worker long afterwards needs no context to be correct.

`Notification` itself is scoped by recipient — a notification belongs to a
*user*, not to an employee, because Admin and CEO hold no employee record and
must still be told things. It carries the organization column anyway, so the
fail-closed manager, the audit trail and the export paths all agree with the
rest of the product.

---

## 6. What proves it

| Test | What it establishes |
|---|---|
| `tests/platform/test_notification_isolation.py` | Each of the nine fan-out events, fired for one organization with the other's people in the same database, produces no notification and no email addressed to them |
| — its positive control | Each case notifies **somebody** in its own organization, so no case passes by notifying nobody |
| — its completeness guard | An AST scan finds every function in `events.py` that resolves an audience and fails if one is not in the matrix |
| — its arity guard | An AST scan of **all of `apps/`** fails on any call that names a permission without naming a tenant, wherever it lives |
| — its signature guard | The organization is a required positional argument, so it cannot be forgotten |
| `tests/platform/test_email_isolation.py` | Override wording reaches one organization only; two organizations may override the same message; a tenant with no mail settings falls back to the platform and never to another tenant; delivery rows cannot be stamped with a foreign organization |
| `tests/platform/test_org_config.py` | No code outside the resolver reads a per-organization mail or device setting directly |

The matrix was verified to bite: with both layers removed, eight of the nine
cases fail on the cross-tenant assertion and the ninth on stamping.

---

## 7. Known limits

- **In-app notifications have no retry.** A failed email delivery is recorded
  as `FAILED` with the error, and there is no button to try again. Candidate
  correspondence does have one. Adding it to notifications is a new action on
  an existing row, not a new mechanism, and the organization is already stored
  where the retry would read it.
- **Template overrides have no editing surface yet.** The model, the resolver
  and the isolation are in place; the settings screen belongs with the rest of
  the frontend work.
- **Three notification kinds have no sender.** `DOCUMENT_EXPIRING`,
  `ONBOARDING_OVERDUE` and `ASSET_RETURN_DUE` are declared and reachable but
  nothing raises them; they are the scheduled reminders that arrive with the
  Celery work. No task calls `notify()` today, so the addressing guard's
  unbound branch is defensive rather than load-bearing — it becomes
  load-bearing the moment a task does, which is what the next slice adds.
