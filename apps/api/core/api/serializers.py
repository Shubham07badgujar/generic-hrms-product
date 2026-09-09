"""
Serializer support for organization-scoped uniqueness.

Business keys are unique PER ORGANIZATION now, expressed as
`UniqueConstraint(fields=["organization", ...])`. DRF builds a
`UniqueTogetherValidator` from such a constraint only when every field in it is
present on the serializer -- and `organization` never is, because it is
`editable=False` and filled in by the model.

The consequence is not cosmetic. Without a validator the duplicate reaches
Postgres, the constraint fires, and the API answers 500 to what is an ordinary
user mistake -- typing an asset tag that already exists. It used to answer 400
with the offending field named, because `unique=True` on a single field gives
DRF a validator for free.

`OrgScopedUniqueMixin` supplies the missing field as a hidden one defaulting to
the caller's organization. DRF then assembles its own validator, so the 400
comes back with field-level detail and no bespoke validation logic exists to
drift from the constraint it mirrors.
"""

from __future__ import annotations

from django.db.models import UniqueConstraint
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator


class CurrentOrganization:
    """
    Default that resolves to the requesting principal's organization.

    Never read from the payload: the client does not get to say which
    organization it is checking uniqueness against, any more than it gets to
    say which organization it is reading from.
    """

    requires_context = True

    def __call__(self, serializer_field):
        from apps.organization.models import Organization
        from core.access.context import get_context

        request = serializer_field.context.get("request")
        if request is None:
            return None
        org_id = get_context(request).organization_id
        if org_id is None:
            return None
        # The INSTANCE, not the id: this value is assigned to a foreign key as
        # well as used by the validator, and Django refuses a raw pk there.
        # Memoised on the request, because a serializer may build this default
        # once per field and there is no reason to re-fetch the same row.
        cached = getattr(request, "_current_organization", None)
        if cached is None or cached.pk != org_id:
            cached = Organization.objects.filter(pk=org_id).first()
            request._current_organization = cached
        return cached

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class OrgScopedUniqueMixin:
    """
    Adds a hidden `organization` field when the model has an organization-scoped
    unique constraint, so DRF can validate it.

    Derived from the model's own constraints rather than declared per
    serializer: a constraint added later is covered without anybody
    remembering, and one removed stops being validated for the same reason.
    """

    def get_fields(self):
        fields = super().get_fields()
        model = getattr(getattr(self, "Meta", None), "model", None)
        if model is None or "organization" in fields:
            return fields

        scoped = any(
            isinstance(c, UniqueConstraint) and "organization" in (c.fields or ())
            for c in model._meta.constraints
        )
        if scoped:
            fields["organization"] = serializers.HiddenField(
                default=CurrentOrganization()
            )
        return fields

    def validate(self, attrs):
        """
        Drop the injected organization once it has served its purpose.

        It exists to let DRF build the validator, and validators have already
        run by the time this is called. Leaving it in `validated_data` would
        push it into every consumer -- `create()`, and the service functions
        the viewsets delegate to, which take explicit keyword arguments and
        raise TypeError on an unexpected one.

        Nothing is lost: the model stamps `organization` in `save()` from the
        acting context, which is the same value and a more trustworthy source
        than anything that travelled through a request body.
        """
        attrs = super().validate(attrs)
        if isinstance(attrs, dict):
            attrs.pop("organization", None)
        return attrs

    def get_unique_together_validators(self):
        """Swap DRF's validator for one that blames the right field."""
        return [
            OrgScopedUniqueValidator(
                queryset=v.queryset, fields=v.fields
            )
            if isinstance(v, UniqueTogetherValidator) and "organization" in v.fields
            else v
            for v in super().get_unique_together_validators()
        ]


class OrgScopedUniqueValidator(UniqueTogetherValidator):
    """
    Reports a scoped-uniqueness failure against the field the user typed.

    DRF's own message would be "The fields organization, asset_tag must make a
    unique set", filed under `non_field_errors`. Two things wrong with that on
    a form: it names an internal column the caller never sent and cannot see,
    and it detaches the error from the input that caused it, so the UI has
    nowhere to put it.

    The organization is not part of the user's mistake -- it is the scope the
    mistake was made in. So the error is keyed to the business field, and reads
    the way it did when the column was globally unique.
    """

    def __call__(self, attrs, serializer):
        try:
            super().__call__(attrs, serializer)
        except serializers.ValidationError:
            business = [f for f in self.fields if f != "organization"]
            message = (
                f"This {business[0].replace('_', ' ')} is already in use."
                if len(business) == 1
                else "These values are already in use together."
            )
            raise serializers.ValidationError(
                {field: message for field in business}, code="unique"
            ) from None


class _OrganizationOnly:
    """Minimal stand-in for an AccessContext when there is no request."""

    __slots__ = ("organization_id", "user_id")

    def __init__(self, organization_id):
        self.organization_id = organization_id
        self.user_id = None


def scope_relation_queryset(queryset, context):
    """
    Narrow a relation lookup to the acting organization.

    Resolved at field-build time, which DRF performs per serializer INSTANCE,
    so the queryset reflects the caller rather than whatever organization
    happened to exist when the class was imported.
    """
    from core.access.context import get_context
    from core.access.engine import apply_org_predicate
    from core.middleware import get_current_org_id

    request = context.get("request") if context else None
    if request is not None:
        return apply_org_predicate(queryset, get_context(request))
    # No request: a service or a management command. `acting_as` binds the
    # organization on those paths, and apply_org_predicate returns nothing when
    # nothing is bound -- which is the right direction for a lookup that
    # decides what a write may point at.
    return apply_org_predicate(queryset, _OrganizationOnly(get_current_org_id()))


class ScopedRelationsMixin:
    """
    Every auto-generated relation lookup is confined to one organization.

    THE WRITE-SIDE HOLE. `ModelSerializer` builds a
    `PrimaryKeyRelatedField(queryset=Model.objects.all())` for each writable
    foreign key -- automatically, invisibly, and about 77 times in this
    codebase. Grepping for `queryset=` finds a fraction of them, because the
    dangerous ones are never written down anywhere.

    Each is a lookup across EVERY organization's rows. Without this, a POST
    naming another company's department id is accepted and the row is created
    pointing across the tenant boundary: a person placed in a company that
    never hired them, and -- since department drives department-scoped
    visibility -- a stranger appearing in that company's headcount.

    Reads were already isolated when this landed. Writes were not, and the two
    are separate claims.
    """

    def build_relational_field(self, field_name, relation_info):
        field_class, field_kwargs = super().build_relational_field(
            field_name, relation_info
        )
        queryset = field_kwargs.get("queryset")
        if queryset is not None:
            field_kwargs["queryset"] = scope_relation_queryset(queryset, self.context)
        return field_class, field_kwargs
