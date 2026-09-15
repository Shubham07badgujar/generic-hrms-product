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


class TenancyError(RuntimeError):
    """Base for tenancy faults. Always a programming error, never user input."""


class OrgMismatch(TenancyError):
    """
    Raised when a row's organization disagrees with its parent's.

    The column is denormalized onto every table so the tenant predicate can be
    one literal string everywhere. The price of that is drift, and this is
    where it is refused: a Payslip whose organization differs from its
    PayrollRun's is exactly the state that made the 2026 incident possible, so
    it cannot be written.
    """


class OrgContextMissing(TenancyError):
    """
    Raised when tenant-scoped data is queried or written with no organization
    bound.

    Deliberately an exception rather than an empty queryset. The 2026 incident
    was a tenant manager that failed OPEN; the obvious over-correction is one
    that fails SILENTLY, and both are invisible. An empty result inside a
    Celery task is indistinguishable from "no work to do", so the bug survives
    for as long as nobody audits the rows that should have changed. An
    exception is the only failure mode a developer cannot ignore.
    """


class TenantScopedManagerMixin:
    """
    The tenant predicate, applied at the manager. Fails closed.

    This is the layer that covers what the view layer cannot. `scope_queryset()`
    protects endpoints, but two thirds of this codebase's queryset call sites
    are in `services/`, reached from management commands and Celery as well as
    HTTP, and DRF additionally builds an unfiltered `Model.objects.all()` behind
    every writable relational serializer field. Those never pass through a view
    mixin. They do pass through here.

    Active only for apps named in `STRICT_TENANT_APPS`, because flipping it
    changes the default behaviour of every query in an app at once. See that
    set for why the rollout is per app.

    The escape hatch is `Model.objects.all_orgs()` -- explicit, greppable, and
    reviewable. There is deliberately no ambient "disable tenancy" switch: a
    flag someone can set at a distance is how the original incident happened.
    """

    def _is_strict(self) -> bool:
        from core.access.tenancy import STRICT_TENANT_APPS

        return self.model._meta.app_label in STRICT_TENANT_APPS

    def get_queryset(self):
        qs = super().get_queryset()
        if not self._is_strict():
            return qs

        from core.middleware import get_current_org_id

        org_id = get_current_org_id()
        if org_id is None:
            raise OrgContextMissing(
                f"{self.model._meta.label}.objects was used with no organization "
                f"bound. Inside a request the access layer binds one from the "
                f"authenticated principal; outside one, wrap the caller in "
                f"acting_as(user, organization=...). If this really is a "
                f"platform-wide query, say so with "
                f"{self.model._meta.label}.objects.all_orgs()."
            )
        return qs.filter(organization_id=org_id)

    def all_orgs(self):
        """
        Every row, every organization. Explicit and greppable.

        For platform administration, data migrations and the isolation tests
        themselves, which must start from an unfiltered queryset in order to
        prove the filtering works. Each call site is a place someone
        deliberately stepped outside tenancy, which is why this is a named
        method and not a keyword argument -- `grep -rn all_orgs` is the audit.
        """
        return super().get_queryset()


class OrgStampingQuerySetMixin:
    """
    `bulk_create` stamps the organization as well.

    `bulk_create` deliberately does not call `save()`, so every model that
    fills its organization in `save()` inserts NULL through it and dies on the
    not-null constraint. That is how 132 payroll failures arrived from a single
    `bulk_create` of salary-structure lines, and 176 more from import staging
    rows.

    Failing on the constraint beats inserting unowned rows, but the caller
    should not have to care which write path they used.
    """

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            if getattr(obj, "organization_id", None) is None:
                obj._stamp_organization({})
        return super().bulk_create(objs, *args, **kwargs)


class OrgStampingMixin:
    """
    Fills in `organization` on the way to the database, and refuses drift.

    Shared by both org-owned bases. It lived on `OrgOwnedModel` alone at first,
    which meant the two `OrgOwnedTimestampedModel` tables -- biometric punches
    and candidate-import staging rows, both high-volume and both full of PII --
    carried the column but never populated it, and every insert failed on the
    not-null constraint. Two bases with one behaviour between them is one base
    too few.
    """

    #: Name of the FK this row inherits its organization from. `None` means the
    #: row is a root -- it belongs to an organization directly, and `save()`
    #: takes the organization from the acting context.
    org_source: str | None = None

    def _parent_organization_id(self):
        """The organization this row inherits, or None if it is a root."""
        if not self.org_source:
            return None
        parent = getattr(self, self.org_source, None)
        return getattr(parent, "organization_id", None) if parent else None

    @staticmethod
    def _also_write_organization(kwargs) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"organization"}

    def _stamp_organization(self, kwargs) -> None:
        parent_org_id = self._parent_organization_id()

        if parent_org_id is not None:
            if self.organization_id is None:
                self.organization_id = parent_org_id
                self._also_write_organization(kwargs)
            elif self.organization_id != parent_org_id:
                raise OrgMismatch(
                    f"{self._meta.label}.organization ({self.organization_id}) "
                    f"disagrees with its {self.org_source}'s organization "
                    f"({parent_org_id}). A child row cannot belong to a "
                    f"different organization than its parent."
                )
            return

        if self.organization_id is None:
            from core.middleware import get_current_org_id

            self.organization_id = get_current_org_id()
            if self.organization_id is None:
                raise OrgContextMissing(
                    f"Cannot save {self._meta.label}: no organization. "
                    + (
                        f"Its `org_source` is '{self.org_source}', which is "
                        f"unset or carries no organization."
                        if self.org_source
                        else "It is a root record, so the organization comes "
                        "from the acting context -- bind one with "
                        "acting_as(user, organization=...)."
                    )
                )
            self._also_write_organization(kwargs)


class OrgOwnedQuerySet(OrgStampingQuerySetMixin, SoftDeleteQuerySet):
    """Soft-delete semantics, plus organization stamping on bulk writes."""


class OrgOwnedManager(
    TenantScopedManagerMixin, models.Manager.from_queryset(OrgOwnedQuerySet)
):
    """
    Default manager for organization-owned models.

    Stamps an organization on write always; FILTERS by one only for apps named
    in `STRICT_TENANT_APPS`. There is one manager rather than two so that
    flipping an app cannot mean swapping a class somewhere and missing a model
    -- the behaviour is a property of the app label, checked on every query.
    """


class OrgOwnedModel(OrgStampingMixin, BaseModel):
    """
    Abstract base for every organization-owned table.

    Carries `organization` on the row itself rather than resolving it through a
    relationship. That is a deliberate denormalization: with the column on every
    table the tenant predicate is one literal string everywhere, so no per-model
    path can be mistyped into an open filter, and the system check that proves
    coverage becomes decidable ("does this model have the column?") instead of a
    path-validation exercise.

    The drift that denormalization usually invites is closed on the write path:
    a derived model names its parent in `org_source`, and `save()` copies the
    organization from it, so a Payslip cannot disagree with its PayrollRun.
    """

    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        db_index=True,
        editable=False,
    )

    objects = OrgOwnedManager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self._stamp_organization(kwargs)
        super().save(*args, **kwargs)


class OrgOwnedTimestampedQuerySet(OrgStampingQuerySetMixin, models.QuerySet):
    """Organization stamping on bulk writes, without soft-delete semantics."""


class OrgOwnedTimestampedManager(
    TenantScopedManagerMixin,
    models.Manager.from_queryset(OrgOwnedTimestampedQuerySet),
):
    """
    Same rule, for the append-only tables.

    These are the two highest-volume tables in the product and both are full of
    PII -- biometric punches and candidate import staging rows -- so leaving
    them on a looser manager than everything else would put the leak where the
    data is densest.
    """


class OrgOwnedTimestampedModel(OrgStampingMixin, TimestampedModel):
    """
    `OrgOwnedModel` for the append-only, high-volume tables that deliberately
    skip soft delete and actor attribution -- biometric punches and import
    staging rows. They hold PII and must be tenant-scoped like everything else.
    """

    organization = models.ForeignKey(
        "organization.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        db_index=True,
        editable=False,
    )

    #: Stamps the organization on bulk writes. These two tables are exactly
    #: the ones written in bulk -- punches arrive from a device sync, import
    #: rows from a spreadsheet -- so `save()` alone would never run.
    #:
    #: Carries `all_orgs()` for the same reason `OrgOwnedManager` does: the
    #: escape hatch has ONE name across the codebase, so code written against
    #: it does not break on whichever models happen to use a different base.
    objects = OrgOwnedTimestampedManager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self._stamp_organization(kwargs)
        super().save(*args, **kwargs)


def org_scoped(model, organization=None):
    """
    Rows of `model` belonging to ONE organization -- the bound one by default.

    For the seed and configuration code that runs OUTSIDE a request, where the
    read-path predicate in `core.access.engine` never applies. Still worth
    calling even for an app that now filters at the manager: it makes the
    organization explicit at the call site rather than ambient, and a bare
    `Model.objects.update_or_create(code=...)` in an app not yet flipped
    matches on a code that is unique only per organization -- and finds
    somebody else's row.

    That is not hypothetical. Every configuration seed except `seed_roles` did
    exactly this, so provisioning a second customer UPDATED the first
    customer's leave types, document types, letter templates, asset categories,
    hiring workflows and clearance template, and left the second customer with
    none of them. `update_or_create` reports success either way, so the only
    symptom was a new company whose configuration screens were empty.

    Raises rather than returning everything when no organization is bound: an
    unscoped write here is the failure this exists to prevent, and a silent
    fallback to "all rows" would reintroduce it at the first call site that
    forgot to bind.
    """
    from core.middleware import get_current_org_id

    organization_id = (
        getattr(organization, "pk", organization)
        if organization is not None
        else get_current_org_id()
    )
    if organization_id is None:
        raise OrgContextMissing(
            f"Cannot scope {model._meta.label} to an organization: none is "
            f"bound. Wrap the call in acting_as(user, organization=...), or "
            f"pass the organization explicitly."
        )
    return model.objects.filter(organization_id=organization_id)
