"""
Creating and reading notifications.

One rule shapes this module: **notifying must never break the thing it is
notifying about**. A payroll approval that failed because the notification
table was busy would be a far worse defect than a missing notification, so
every failure here is logged and swallowed. The caller gets its work done.

The second rule is that a notification is addressed to a USER, never to an
Employee. Admin and CEO hold no Employee record and still need to be told
things.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationKind,
    NotificationPreference,
    Priority,
)
from .transports import get_transports

logger = logging.getLogger("hrms.notifications")


def notify(
    *,
    recipient,
    kind: str,
    title: str,
    body: str = "",
    link_url: str = "",
    priority: str = Priority.NORMAL,
    entity=None,
    dedupe_key: str = "",
) -> Notification | None:
    """
    Tell one person one thing. Returns None if nothing was created.

    Returning None is a normal outcome, not a failure: the recipient may have
    switched the kind off, or an identical unread notification may already be
    sitting in their list.
    """
    if recipient is None or not getattr(recipient, "is_active", False):
        return None

    entity_type, entity_id = "", ""
    if entity is not None:
        entity_type = f"{entity._meta.app_label}.{entity.__class__.__name__}"
        entity_id = str(entity.pk)

    try:
        with transaction.atomic():
            if dedupe_key and Notification.objects.filter(
                recipient=recipient, dedupe_key=dedupe_key, is_read=False, is_active=True
            ).exists():
                # The same pending fact, still unread. A second row would not
                # tell them anything the first one has not already.
                return None

            wants_in_app, wants_email = _preferences_for(recipient, kind, priority)
            if not wants_in_app and not wants_email:
                return None

            notification = Notification.objects.create(
                recipient=recipient,
                kind=kind,
                title=title[:200],
                body=body,
                link_url=link_url[:400],
                priority=priority,
                entity_type=entity_type,
                entity_id=entity_id,
                dedupe_key=dedupe_key[:200],
            )

        _deliver(notification, wants_in_app=wants_in_app, wants_email=wants_email)
        return notification

    except Exception:
        logger.exception("notifications.create_failed kind=%s recipient=%s", kind, recipient.pk)
        return None


def notify_many(*, recipients, **kwargs) -> list[Notification]:
    """Same notification to several people, skipping duplicates and Nones."""
    seen, created = set(), []
    for recipient in recipients:
        if recipient is None or recipient.pk in seen:
            continue
        seen.add(recipient.pk)
        notification = notify(recipient=recipient, **kwargs)
        if notification is not None:
            created.append(notification)
    return created


def _preferences_for(recipient, kind: str, priority: str) -> tuple[bool, bool]:
    """
    What this user wants for this kind.

    CRITICAL bypasses preference entirely. A blocked payroll run or an overdue
    clearance is not a matter of taste — someone has to act, and letting it be
    switched off turns a preference into a way to miss an obligation.
    """
    if priority == Priority.CRITICAL:
        return True, False

    preference = NotificationPreference.objects.filter(user=recipient, kind=kind).first()
    if preference is None:
        # Never configured. On by default — defaulting to silence would make
        # the system depend on people finding a settings screen.
        return True, False
    return preference.in_app, preference.email


def _deliver(notification, *, wants_in_app: bool, wants_email: bool) -> None:
    wanted = {"in_app": wants_in_app, "email": wants_email}

    for transport in get_transports():
        if not wanted.get(transport.name, False):
            NotificationDelivery.objects.create(
                notification=notification,
                channel=transport.name,
                status=DeliveryStatus.SUPPRESSED,
            )
            continue
        try:
            transport.send(notification)
            NotificationDelivery.objects.create(
                notification=notification,
                channel=transport.name,
                status=DeliveryStatus.SENT,
                sent_at=timezone.now(),
            )
        except Exception as exc:
            logger.warning(
                "notifications.delivery_failed channel=%s id=%s", transport.name, notification.pk
            )
            NotificationDelivery.objects.create(
                notification=notification,
                channel=transport.name,
                status=DeliveryStatus.FAILED,
                error=str(exc)[:1000],
            )


# ---------------------------------------------------------------- reading


def visible_to(user):
    """
    The notifications this user may see: their own, and only their own.

    Not routed through `scope_queryset`. A notification belongs to a User, and
    the employee-path scoper cannot express that — Admin and CEO have no
    Employee row, so any employee-based scoping would silently hide every
    notification from exactly the two principals who most need them.
    """
    return Notification.objects.filter(recipient=user, is_active=True)


def mark_read(notification, *, user) -> Notification:
    """
    Mark one notification read.

    Ownership is the authority here, not an RBAC edit grant. A read receipt is
    the reader's own view state, not a business record — and gating it on
    NOTIFICATION/EDIT would mean the CEO, whose write actions are stripped by
    the read-only clamp, could never clear their own notification list.
    """
    if notification.recipient_id != user.pk:
        raise PermissionError("A notification can only be read by its recipient.")
    if notification.is_read:
        return notification

    notification.is_read = True
    notification.read_at = timezone.now()
    notification.save(update_fields=["is_read", "read_at", "updated_at"])
    return notification


def mark_all_read(user) -> int:
    return visible_to(user).filter(is_read=False).update(
        is_read=True, read_at=timezone.now()
    )


def unread_count(user) -> int:
    return visible_to(user).filter(is_read=False).count()


def set_preference(*, user, kind: str, in_app: bool, email: bool) -> NotificationPreference:
    if kind not in NotificationKind.values:
        raise ValueError(f"'{kind}' is not a notification kind.")

    preference, _ = NotificationPreference.objects.update_or_create(
        user=user, kind=kind, defaults={"in_app": in_app, "email": email}
    )
    return preference
