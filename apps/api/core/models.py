"""
Shared model base classes.

Every domain table inherits `BaseModel`: UUID primary key, creation/update
timestamps, actor attribution, and soft delete.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    """Queryset with soft-delete semantics."""

    def delete(self):
        """Soft-delete in bulk. Use `hard_delete()` when you truly mean it."""
        return self.update(is_active=False, updated_at=timezone.now())

    def hard_delete(self):
        return super().delete()

    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)


class BaseManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """
    Default manager.

    Note this returns ALL rows including soft-deleted ones. Filtering to active
    rows is an explicit `.active()` call, because several screens (archived
    locations, historical allocations) legitimately need the full set, and a
    manager that silently hides rows makes those bugs very hard to see.
    """


class BaseModel(models.Model):
    """
    Abstract base for every domain table.

    There is deliberately NO `organization` field: this system serves a single
    organization. See config/settings/base.py for why tenancy was removed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    is_active = models.BooleanField(default=True, db_index=True)

    objects = BaseManager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        """Attribute the write to the acting user, when one is known."""
        from core.middleware import get_current_user

        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.add("updated_by")
                if self._state.adding:
                    update_fields.add("created_by")
                kwargs["update_fields"] = update_fields
        super().save(*args, **kwargs)

    def delete(self, *args, hard: bool = False, **kwargs):
        """Soft-delete by default. `hard=True` for genuine removal."""
        if hard:
            return super().delete(*args, **kwargs)
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])
        return (0, {})


class TimestampedModel(models.Model):
    """
    Timestamps without soft delete or actor attribution.

    For append-only or high-volume rows where the `BaseModel` machinery is
    overhead rather than help (e.g. notification delivery attempts).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = models.Manager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]
