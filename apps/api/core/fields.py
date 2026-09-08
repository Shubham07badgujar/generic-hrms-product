"""
Encrypted model fields for PII at rest, plus masking helpers.

Encrypts PAN, Aadhaar and bank account numbers with Fernet (AES-128-CBC +
HMAC-SHA256). Values are decrypted transparently on read.

TRADE-OFF, STATED PLAINLY
------------------------
Application-level encryption means these columns cannot be filtered, sorted or
indexed in SQL — `WHERE pan = ?` is impossible, because every row has a
different ciphertext for the same plaintext. That is accepted: these fields are
displayed and exported, never searched. If exact-match lookup is ever needed,
add a separate blind-index column (HMAC of the normalized value) rather than
weakening this.

KEY MANAGEMENT
--------------
`FIELD_ENCRYPTION_KEY` must be backed up outside the database. Losing it makes
every encrypted column permanently unreadable — there is no recovery path.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property


class _FernetMixin:
    """Shared encrypt/decrypt plumbing."""

    @cached_property
    def _fernet(self) -> Fernet:
        key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        if not key:
            raise ImproperlyConfigured(
                "FIELD_ENCRYPTION_KEY is not set. Encrypted fields cannot be "
                "read or written without it."
            )
        if isinstance(key, str):
            key = key.encode()
        try:
            return Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ImproperlyConfigured(
                "FIELD_ENCRYPTION_KEY is not a valid Fernet key. Generate one "
                "with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ) from exc

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        if isinstance(value, str):
            value = value.encode()
        return self._fernet.encrypt(value).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            # A row written under a different key, or plaintext left by a bad
            # migration. Never crash a whole list view over one bad row — but
            # never silently show the ciphertext as if it were the value.
            return None


class EncryptedCharField(_FernetMixin, models.TextField):
    """
    Encrypted short string.

    Backed by TEXT rather than VARCHAR(n): ciphertext is substantially longer
    than plaintext and its length varies, so a max_length on the column would
    truncate. Length limits belong in the serializer.
    """

    description = "Fernet-encrypted character field"

    def __init__(self, *args, **kwargs):
        # Accepted for readability at the call site, but not passed to the DB.
        self.plaintext_max_length = kwargs.pop("plaintext_max_length", None)
        kwargs.pop("max_length", None)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plaintext_max_length is not None:
            kwargs["plaintext_max_length"] = self.plaintext_max_length
        return name, path, args, kwargs


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------
# Masked forms are what appear in UI and exports. Full values are released only
# where legally required, and every such access is written to the audit log.


def mask_aadhaar(value: str | None) -> str:
    """`XXXX XXXX 1234` — last 4 only. Aadhaar must never be shown in full."""
    if not value:
        return ""
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) < 4:
        return "XXXX XXXX XXXX"
    return f"XXXX XXXX {digits[-4:]}"


def mask_pan(value: str | None) -> str:
    """`ABCXX1234X` — keeps the first 3 and last 4, which is enough to verify."""
    if not value:
        return ""
    v = value.strip().upper()
    if len(v) != 10:
        return "XXXXXXXXXX"
    return f"{v[:3]}XX{v[5:9]}X"


def mask_bank_account(value: str | None) -> str:
    """`XXXXXX7890` — last 4 only."""
    if not value:
        return ""
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) < 4:
        return "XXXXXXXX"
    return f"{'X' * max(len(digits) - 4, 4)}{digits[-4:]}"


def fingerprint(value: str | None) -> str | None:
    """
    Deterministic, non-reversible fingerprint for duplicate detection.

    Lets the system answer "is this the same PAN as an existing employee?"
    without storing or comparing plaintext. Salted with SECRET_KEY so the
    digests are useless outside this deployment.
    """
    if not value:
        return None
    normalized = "".join(value.split()).upper()
    salted = f"{settings.SECRET_KEY}:{normalized}".encode()
    return base64.b16encode(hashlib.sha256(salted).digest()).decode()[:64]
