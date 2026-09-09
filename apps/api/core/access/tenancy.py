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
    "audit.AuditLog": (
        "Carries a NULLABLE organization instead: pre-authentication events, "
        "platform actions and organization creation itself genuinely have "
        "none. Scoped by an explicit predicate, never by the shared one."
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


def is_tenanted(model) -> bool:
    """Whether this model actually carries an organization column."""
    return any(f.name == "organization" for f in model._meta.get_fields())
