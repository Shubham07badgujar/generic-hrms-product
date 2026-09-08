"""
Audit registration.

Each app registers its models in `AppConfig.ready()`; `apps.audit` is LAST in
INSTALLED_APPS so every registration has already run when it connects signals.

    register(User, redact={"password", "mfa_secret"})

Redacted fields are recorded as *changed* but never with their value — you
learn that a PAN was edited, never what it was.
"""

from __future__ import annotations

_REGISTRY: dict[type, dict] = {}


def register(model, *, redact: set[str] | None = None):
    """Register `model` for automatic create/update/delete auditing."""
    _REGISTRY[model] = {"redact": set(redact or set())}
    return model


def audited(*, redact: set[str] | None = None):
    """Class-decorator form of `register`."""

    def wrapper(model):
        return register(model, redact=redact)

    return wrapper


def is_registered(model) -> bool:
    return model in _REGISTRY


def options_for(model) -> dict:
    return _REGISTRY.get(model, {"redact": set()})


def registered_models():
    return list(_REGISTRY.keys())
