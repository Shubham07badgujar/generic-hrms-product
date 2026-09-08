"""
Outbound mail must never be able to block a thread forever.

This is a regression test for a hang found during end-to-end recruitment
testing: the scheduling flow stopped dead for over half an hour with no
database activity and no error, blocked inside a single `message.send()`.

Every sender in this codebase is written to survive a mail failure — the
outcome is recorded on the notification row and the caller carries on. But a
HANG is not a failure: without a socket timeout the `except` is never reached,
the request never returns, and enough of them exhaust the worker pool. Django
defaults EMAIL_TIMEOUT to None, meaning no timeout, so this has to be set
explicitly and stay set.
"""

from __future__ import annotations

from django.conf import settings


def test_smtp_conversations_have_a_timeout():
    timeout = getattr(settings, "EMAIL_TIMEOUT", None)
    assert timeout is not None, (
        "EMAIL_TIMEOUT is unset, so Django gives the SMTP socket no timeout: "
        "a stalled mail server blocks the sending thread forever."
    )
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 60, (
        f"EMAIL_TIMEOUT={timeout!r}: keep it a small positive number of "
        f"seconds — long enough for a TLS handshake, short enough that a dead "
        f"mail server cannot tie up a worker."
    )


def test_the_hr_sender_inherits_that_timeout():
    """
    HR's welcome mail uses its own authenticated connection. It passes no
    timeout of its own, which is fine ONLY because Django's SMTP backend falls
    back to EMAIL_TIMEOUT — this pins that it really does.
    """
    from django.core import mail

    connection = mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username="hr@example.test",
        password="x",
        use_tls=settings.EMAIL_USE_TLS,
    )
    assert connection.timeout == settings.EMAIL_TIMEOUT
