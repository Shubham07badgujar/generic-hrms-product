"""
Delivery channels.

This is the seam that keeps polling from becoming a decision we have to undo.
`notify()` writes the row, then offers it to each registered transport. The row
is the source of truth and the in-app channel is a no-op on top of it, so the
SPA's polling endpoint needs no transport at all.

Adding WebSockets later means registering a third transport here. It changes no
model, no service signature, and no caller.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("hrms.notifications")


class Transport(Protocol):
    name: str

    def send(self, notification) -> None:
        """Deliver, or raise. `notify()` records the outcome either way."""


class InAppTransport:
    """
    A no-op by construction.

    The notification row IS the in-app delivery — the SPA reads rows. This
    exists so the in-app channel appears in `NotificationDelivery` alongside
    the others, rather than being an implicit special case the reporting has to
    know about.
    """

    name = "in_app"

    def send(self, notification) -> None:
        return None


class EmailTransport:
    """
    Email via Django's configured backend.

    Sent inline. Celery is configured and `CELERY_TASK_ALWAYS_EAGER` is on in
    dev, so moving this to a task later is a decorator on this method — but a
    notification that quietly fails to send is worse than a slow request, so it
    stays synchronous and recorded until there is a worker to own it.
    """

    name = "email"

    def send(self, notification) -> None:
        from django.core.mail import send_mail
        from django.conf import settings

        recipient = getattr(notification.recipient, "email", "")
        if not recipient:
            raise ValueError("Recipient has no email address.")

        send_mail(
            subject=notification.title,
            message=notification.body or notification.title,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[recipient],
            fail_silently=False,
        )


#: Registered transports, in delivery order.
TRANSPORTS: list[Transport] = [InAppTransport(), EmailTransport()]


def get_transports() -> list[Transport]:
    return list(TRANSPORTS)
