"""
Company email accounts.

The whole surface is metadata. There is no function here that accepts a
password, stores one, or returns one, because there is no field for it — see
the model docstring. HR creates the mailbox in the provider's console and
records the fact here; the credential reaches the employee by a channel this
system is not part of.

`assert_no_credentials_in` is the guard that keeps it that way: a future caller
adding `password` to a payload is refused loudly rather than quietly persisting
it in a notes field.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee
from core.access import Action, Resource, require

from .models import AccountStatus, CompanyEmailAccount

#: Anything that smells like a secret. Checked against payload KEYS.
CREDENTIAL_KEYS = frozenset(
    {"password", "passwd", "pwd", "secret", "credential", "credentials", "token", "api_key"}
)


class AccountError(ValidationError):
    """A refused account operation."""


def assert_no_credentials_in(payload: dict) -> None:
    """
    Refuse any payload carrying a secret.

    Deliberately noisy. The alternative — silently dropping the field — leaves
    whoever sent it believing the system stored their password, which is worse
    than an error.
    """
    offending = sorted(CREDENTIAL_KEYS & {key.lower() for key in payload})
    if offending:
        raise AccountError(
            {
                "detail": (
                    f"This system never stores account credentials, and {', '.join(offending)} "
                    f"cannot be accepted. Create the mailbox in the provider's console and "
                    f"share the password with the employee through your organisation's "
                    f"secure channel."
                )
            }
        )


@transaction.atomic
def record_account(
    *,
    employee: Employee,
    actor,
    email_address: str,
    provider: str,
    external_account_id: str = "",
    notes: str = "",
    **extra,
) -> CompanyEmailAccount:
    """Record a mailbox that HR has created in the provider's console."""
    require(actor, Resource.EMAIL_ACCOUNT, Action.CREATE)
    assert_no_credentials_in(extra)

    if CompanyEmailAccount.objects.filter(employee=employee).exists():
        raise AccountError(
            {"employee": f"{employee.full_name} already has a company account recorded."}
        )

    return CompanyEmailAccount.objects.create(
        employee=employee,
        email_address=email_address.strip().lower(),
        provider=provider,
        external_account_id=external_account_id,
        notes=notes,
        status=AccountStatus.REQUESTED,
        requested_by=actor,
        requested_at=timezone.now(),
    )


@transaction.atomic
def mark_provisioned(*, account: CompanyEmailAccount, actor, external_account_id: str = ""):
    """
    Confirm the mailbox is live.

    Records WHO confirmed it and when — the provisioning trail the spec asks
    for — without ever touching how the employee signs in.
    """
    require(actor, Resource.EMAIL_ACCOUNT, Action.EDIT)

    if account.status == AccountStatus.ACTIVE:
        raise AccountError({"account": "This account is already active."})

    account.status = AccountStatus.ACTIVE
    account.provisioned_by = actor
    account.provisioned_at = timezone.now()
    if external_account_id:
        account.external_account_id = external_account_id
    account.save()

    _satisfy_onboarding_item(account, actor=actor)
    _audit(account, actor=actor, event="account_provisioned")
    return account


@transaction.atomic
def suspend(*, account: CompanyEmailAccount, actor, reason: str = ""):
    require(actor, Resource.EMAIL_ACCOUNT, Action.EDIT)
    account.status = AccountStatus.SUSPENDED
    account.suspended_at = timezone.now()
    if reason:
        account.notes = reason[:255]
    account.save()
    _audit(account, actor=actor, event="account_suspended", extra={"reason": reason})
    return account


@transaction.atomic
def deprovision(*, account: CompanyEmailAccount, actor):
    """Called on exit. The mailbox is closed in the provider's console too."""
    require(actor, Resource.EMAIL_ACCOUNT, Action.EDIT)
    account.status = AccountStatus.DEPROVISIONED
    account.deprovisioned_at = timezone.now()
    account.save()
    _audit(account, actor=actor, event="account_deprovisioned")
    return account


def _satisfy_onboarding_item(account: CompanyEmailAccount, *, actor) -> None:
    from apps.onboarding.models import ItemKind, ItemStatus, OnboardingItem
    from apps.onboarding.services import _maybe_close

    item = (
        OnboardingItem.objects.filter(
            onboarding__employee=account.employee,
            kind=ItemKind.ACCOUNT,
            status__in=[ItemStatus.PENDING, ItemStatus.IN_PROGRESS, ItemStatus.BLOCKED],
        )
        .select_related("onboarding")
        .order_by("order")
        .first()
    )
    if item is None:
        return
    item.status = ItemStatus.COMPLETED
    item.completed_at = timezone.now()
    item.completed_by = actor
    item.notes = account.email_address
    item.save(update_fields=["status", "completed_at", "completed_by", "notes", "updated_at"])
    _maybe_close(item.onboarding, actor=actor)


def _audit(account, *, actor, event: str, extra: dict | None = None) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.UPDATE,
        resource=Resource.EMAIL_ACCOUNT,
        entity_type="itaccounts.CompanyEmailAccount",
        entity_id=str(account.pk),
        entity_label=account.email_address,
        after={
            "event": event,
            "provider": account.provider,
            "status": account.status,
            "employee": account.employee.employee_code,
            **(extra or {}),
        },
        request_id=get_request_id(),
    )
