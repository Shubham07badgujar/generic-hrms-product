"""
A management command that acts on ONE organization.

Every configuration seed writes org-owned rows, and org-owned rows take their
organization from the acting context. Before tenancy that context did not
exist and the commands did not need one; afterwards they still did not ask for
one, so `manage.py seed_leave` -- and `seed_all`, which calls it -- raised
`OrgContextMissing` on the first row. Nothing caught it, because no test runs a
seed command; they are the operator's interface, exercised by hand.

So the option and the binding live here, once, rather than in six copies that
drift. A command inheriting `OrganizationCommand` gets `--organization <slug>`,
the refusal-to-guess rule below, and a bound context for the whole of
`handle_for_organization`.

REFUSING TO GUESS is the rule worth stating. With exactly one organization the
slug is optional, because a self-hosted single-company deployment should not
have to name the only company it has. With several, the command stops: picking
the first would seed one customer's configuration into another's account, and
`update_or_create` on a code that is now unique only per organization would
report success while doing it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.middleware import acting_as


def resolve_organization(slug: str | None):
    """The organization named by `slug`, or the only one, or an error."""
    from apps.organization.models import Organization

    if slug:
        try:
            return Organization.objects.get(slug=slug)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"No organization with slug {slug!r}.") from exc

    existing = list(Organization.objects.all()[:2])
    if len(existing) == 1:
        return existing[0]
    if not existing:
        raise CommandError(
            "No organization exists. Provision one first -- a platform admin "
            "creates organizations, or `bootstrap_admin` creates the founding "
            "Admin and the single organization they administer."
        )
    raise CommandError(
        "Several organizations exist; pass --organization <slug> to say which "
        "one to seed. Refusing to guess: seeding the wrong company's "
        "configuration is silent, because update_or_create reports success "
        "either way."
    )


class OrganizationCommand(BaseCommand):
    """Base for a command whose writes belong to one organization."""

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            metavar="SLUG",
            help=(
                "Slug of the organization to act on. Optional while a "
                "deployment has exactly one; required once it has several, "
                "because there is no sensible default."
            ),
        )

    def handle(self, *args, **options):
        # POPPED, not read: the option's dest is `organization` and the
        # resolved object is passed positionally, so leaving the slug in
        # `options` makes every subclass raise "got multiple values for
        # argument 'organization'".
        organization = resolve_organization(options.pop("organization", None))
        with acting_as(None, organization=organization):
            return self.handle_for_organization(organization, *args, **options)

    def handle_for_organization(self, organization, *args, **options):
        raise NotImplementedError
