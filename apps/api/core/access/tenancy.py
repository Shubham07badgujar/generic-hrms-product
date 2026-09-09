"""
Which models are tenant-scoped, and which are knowingly not yet.

The organization predicate can only filter on a column that exists. During the
conversion most models do not have one yet, so the predicate has to let their
queries through -- and a filter that silently lets things through is precisely
the failure this architecture exists to prevent.

So the transitional state is not implicit. Every model that is not yet
converted is named in `PENDING_TENANCY` below, `manage.py check` fails for any
model that is neither converted, pending, nor deliberately global, and the
count is reported on every build. The set shrinks to empty as apps convert;
when it is empty the transitional branch is dead code and comes out.

The point is that "this table is not protected yet" is a line in a file
somebody has to delete, not an absence nobody notices.
"""

from __future__ import annotations

#: Genuinely global. Not tenant data, and never will be.
TENANT_EXEMPT: dict[str, str] = {
    "organization.Organization": "Is the tenant.",
    "organization.OrganizationMembership": (
        "Resolving a principal's organization is what this table is queried "
        "FOR, so requiring one first would be circular."
    ),
    "accounts.User": (
        "Login identity is global: email is the USERNAME_FIELD and must be "
        "unique platform-wide. A user's tenant comes from their membership."
    ),
    "statutory.StatutoryRuleSet": (
        "PF/ESI/PT/income-tax tables are facts about the Republic of India, "
        "not about a customer. Duplicating them per organization would mean N "
        "copies to verify and would defeat four-eyes verification."
    ),
}

#: Tenant-owned, but the column is NULLABLE -- and NULL means "platform-owned",
#: never "everyone's".
#:
#: `audit.AuditLog` was in TENANT_EXEMPT, on the reasoning that pre-auth events,
#: platform actions and organization creation itself genuinely have no
#: organization, so it needed "an explicit predicate, never the shared one".
#: The explicit predicate was never written. The route walker found the result:
#: one organization's admin reading another's audit trail through
#: `/api/v1/audit/<id>/`, on a resource the brief names by name.
#:
#: The shared predicate turns out to be exactly the explicit one that was
#: wanted. `WHERE organization_id = X` matches no NULL row, so platform events
#: stay invisible to organization users without a special case -- and the
#: dangerous shape, `= X OR IS NULL`, is one nobody has to remember not to
#: write. This set exists only to record that the nullability is deliberate.
NULLABLE_TENANT: dict[str, str] = {
    "audit.AuditLog": (
        "Pre-authentication events, platform actions and organization creation "
        "itself have no organization. They are platform-owned, and no "
        "organization reads them."
    ),
}

#: NOT YET CONVERTED -- and now empty.
#:
#: This held 91 tables whose rows are tenant data but which did not yet carry
#: the column, so queries against them were NOT organization-scoped. It existed
#: so that "unprotected" was a list somebody had to delete rather than an
#: absence nobody noticed, and `access.E005` made it impossible for a new model
#: to join the set by accident.
#:
#: It is empty because every one of them was converted. Keep the mechanism: it
#: costs nothing, and it is what will catch the next model added without a
#: tenancy decision.
PENDING_TENANCY: frozenset[str] = frozenset()


#: Values that are deliberately unique across the WHOLE platform, with the
#: reason. Everything else on a tenant-owned table must be scoped -- either by
#: naming `organization` in the constraint, or by being unique through a
#: relation that is itself organization-owned.
GLOBALLY_UNIQUE: dict[str, str] = {
    "recruitment.JobOpening.application_token": (
        "A capability: the token IS the authorisation for the public "
        "application form. Two organizations sharing one would be a "
        "cross-tenant hole, so global uniqueness is the requirement, not a "
        "leftover."
    ),
    "recruitment.InterviewSlotInvite.token": "Same: a capability in a URL.",
    "recruitment.CandidateNotification.dedupe_key": (
        "Derived from the application id, which is already organization-owned."
    ),
    "organization.OrgSettings.organization": "IS the organization link.",
}

#: Unique through `User`, which is deliberately global.
#:
#: These are tenant-safe ONLY because of the V1 clamp: a user holds at most one
#: ACTIVE OrganizationMembership, so "per user" and "per user per organization"
#: are the same statement. That is a real dependency, not a coincidence.
#:
#: WHEN MULTI-ORGANIZATION MEMBERSHIP ARRIVES, EVERY ENTRY HERE BECOMES WRONG:
#: one person in two companies would get one notification preference and one
#: permission override across both, and could hold only a single Employee
#: record. Revisit this list at the same time as
#: `uniq_one_active_membership`, and not later.
SCOPED_THROUGH_USER: frozenset[str] = frozenset(
    {
        "employees.Employee.user",
        "accounts.UserPermissionOverride.uniq_override_user_res_act",
        "notifications.NotificationPreference.uniq_preference_per_kind",
    }
)


def is_tenanted(model) -> bool:
    """Whether this model actually carries an organization column."""
    return any(f.name == "organization" for f in model._meta.get_fields())
