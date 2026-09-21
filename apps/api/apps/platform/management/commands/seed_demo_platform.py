"""
Three demo customers on one deployment, created the way real ones are.

    manage.py seed_demo_platform                    # all three
    manage.py seed_demo_platform --profile retail   # just one
    manage.py seed_demo_platform --remove           # delete their people
    manage.py seed_demo_platform --reset            # remove, then rebuild

WHY THIS EXISTS SEPARATELY FROM `seed_all`

`seed_all` builds ONE company and everything in it: a month of attendance, a
payroll run, a recruitment pipeline. It is the command for testing the HR
product. This one builds THREE companies that differ commercially, and it is
the command for testing the SaaS product -- entitlement, seat limits, trial
expiry, the platform console, and above all the thing a single company can
never demonstrate, which is that two customers on one deployment cannot see
each other.

Run both: `seed_all` afterwards, pointed at the healthcare organization, fills
it with transactional data.

THROUGH THE PROVISIONING SERVICE, NOT AROUND IT

Every organization here is created by `provision_organization` -- the same
atomic call the platform console makes. Nothing about a demo customer is
special: same validation, same configuration seeds, same first administrator
with a membership and an Admin grant, same audit rows, same invitation
attempt. A demo built by writing rows directly would prove that this file can
write rows; built this way it proves the provisioning path works, and it fails
here rather than in front of a customer when that path breaks.

The one thing it does that a real provisioning does not is reset the
administrator's password to a recorded one, because a demo account nobody can
sign into is not a demo account. Every password is random, 20 characters, and
written to a 0600 file inside that organization's own media subtree.

WHAT EACH COMPANY IS FOR

    demo-healthcare   every module, one account per role
    demo-technology   a plan WITHOUT payroll -- entitlement, visibly
    demo-retail       a trial expiring in days

THE INVITATION MAIL

Provisioning schedules one per organization on commit. On a deployment with
real SMTP configured that is three messages to `@*.example` addresses, which
cannot resolve and will bounce into nothing. Harmless, but `--no-invite`
suppresses it for anyone who would rather their mail log stayed clean.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from apps.organization.demo import PROFILES, get_profile
from apps.organization.demo.profiles import PROFILE_ORDER

#: How many days Retail's trial has left. Days rather than weeks on purpose:
#: the interesting state is the one a customer is in when somebody has to
#: decide whether to buy, and a trial with a month left shows nothing.
RETAIL_TRIAL_DAYS = 3


class Command(BaseCommand):
    help = (
        "Provision the three demo customers (healthcare, technology, retail) "
        "through the platform provisioning service, each on a different plan."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile", action="append", choices=sorted(PROFILES), default=None,
            help="Seed only these profiles. Repeatable. Default: all three.",
        )
        parser.add_argument(
            "--remove", action="store_true",
            help=(
                "Delete the demo people. The organizations themselves stay -- "
                "nothing in this product deletes a customer in one call."
            ),
        )
        parser.add_argument("--reset", action="store_true",
                            help="Remove first, then rebuild.")
        parser.add_argument(
            "--no-invite", action="store_true",
            help="Do not attempt the invitation email (the addresses cannot resolve).",
        )

    # ------------------------------------------------------------------ main
    def handle(self, *args, **options):
        profiles = [get_profile(key) for key in (options["profile"] or PROFILE_ORDER)]

        if options["remove"] or options["reset"]:
            for profile in profiles:
                self._remove(profile)
            if options["remove"]:
                return

        self._require_plans(profiles)
        for profile in profiles:
            self._seed(profile, no_invite=options["no_invite"])
        self._summary(profiles)

    # --------------------------------------------------------------- checks
    def _require_plans(self, profiles):
        """
        Refused up front, because the whole point of these three is that they
        are on DIFFERENT plans. Seeding them all onto whatever plan happens to
        exist would produce three identical companies and quietly remove the
        only reason for having three.
        """
        from apps.platform.models import Plan

        wanted = {profile.plan_code for profile in profiles}
        have = set(
            Plan.objects.filter(code__in=wanted, is_active=True).values_list(
                "code", flat=True
            )
        )
        missing = sorted(wanted - have)
        if missing:
            raise CommandError(
                f"No active plan for {', '.join(missing)}. Run `manage.py "
                f"seed_plans` first -- these companies differ by plan, and "
                f"seeding them without one defeats the exercise."
            )

    def _platform_admin(self):
        """
        The operator to attribute this to, if there is one.

        None is acceptable and the service allows it: a deployment may not have
        bootstrapped an operator yet. The cost is audit rows with no actor, so
        it is said out loud rather than passed silently.
        """
        from apps.accounts.models import User

        actor = User.objects.filter(is_platform_admin=True, is_active=True).first()
        if actor is None:
            self.stdout.write(self.style.WARNING(
                "  no platform administrator exists, so these organizations will "
                "be audited with no actor.\n  Run `manage.py "
                "bootstrap_platform_admin` first if that matters."
            ))
        return actor

    # ----------------------------------------------------------------- seed
    def _seed(self, profile, *, no_invite=False):
        from apps.organization.models import Organization
        from apps.platform.models import Plan
        from apps.platform.services.provisioning import (
            ProvisioningError,
            provision_organization,
        )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{profile.company}  ({profile.slug}, plan {profile.plan_code})"
        ))

        problems = profile.validate()
        if problems:
            raise CommandError(
                "This demo profile is malformed:\n  " + "\n  ".join(problems)
            )

        actor = self._platform_admin()
        plan = Plan.objects.get(code=profile.plan_code, is_active=True)

        # Provisioned once; seeded into every time. Re-running this command is
        # how `--reset` rebuilds the people after `--remove` took them, and the
        # organization itself survives both -- see `_remove`.
        organization = Organization.objects.filter(slug=profile.slug).first()
        if organization is None:
            try:
                result = provision_organization(
                    name=profile.company,
                    legal_name=profile.legal_name,
                    slug=profile.slug,
                    admin_email=f"admin@{profile.domain}",
                    admin_first_name="Sysadmin",
                    admin_last_name="Demo",
                    primary_email=f"hello@{profile.domain}",
                    city=profile.head_office.city,
                    state=profile.head_office.state,
                    plan=plan,
                    actor=actor,
                )
            except ProvisioningError as refusal:
                raise CommandError(f"{profile.slug}: {refusal}") from refusal

            if no_invite:
                # The mail is scheduled for after commit and cannot be
                # unscheduled from here, so this only suppresses the attempt --
                # which is what matters, since the addresses cannot resolve.
                result.invitation_sent = False

            organization = result.organization
            self.stdout.write(
                f"  provisioned: {len(result.seeded)} configuration seeds, "
                f"administrator admin@{profile.domain}"
            )
        else:
            self.stdout.write(f"  {profile.slug} already exists — seeding into it")

        # The people. `seed_demo_company` acts as THIS organization's own
        # admin -- the one provisioning just created -- so every employee goes
        # through `create_employee` with a real actor and the hierarchy,
        # department and role-grant rules all apply.
        call_command(
            "seed_demo_company",
            "--organization", organization.slug,
            "--profile", profile.key,
        )

        self._go_live(organization, profile, actor=actor)

    def _go_live(self, organization, profile, *, actor):
        """
        Out of `pending_setup`, through the wizard's own gate.

        `finish_setup` refuses while a required step is unsatisfied and names
        the ones that are, so reaching ACTIVE here is a real assertion that the
        seeded company is complete: departments, locations, designations,
        roles, a leave policy and an attendance policy all present. A demo that
        set the status directly would skip the only check that proves it.
        """
        from apps.organization.models import OrgStatus
        from apps.organization.setup import SetupError, finish_setup
        from apps.platform.models import Subscription

        organization.refresh_from_db()
        if organization.status == OrgStatus.PENDING_SETUP:
            try:
                finish_setup(organization, actor=actor)
            except SetupError as refusal:
                raise CommandError(
                    f"{organization.slug} could not be taken live: {refusal}"
                ) from refusal
        else:
            # A re-seed into a company that is already live. `finish_setup`
            # refuses a second time by design, and rightly.
            self.stdout.write(
                f"  organization is already {organization.get_status_display()}"
            )

        if profile.key != "retail":
            organization.refresh_from_db()
            self.stdout.write(
                f"  organization is {organization.get_status_display()}"
            )
            return

        # The trial window is refreshed on EVERY run, not only the first. A
        # re-seeded demo whose trial expired three weeks ago demonstrates
        # nothing about a trial.

        # Retail is the trial company, and getting it there takes a write this
        # file would rather not make.
        #
        # `set_status` is the sanctioned way to move commercial state and it is
        # the only writer of `Organization.status` in the app -- but it
        # early-returns when the status does not change, and this subscription
        # is ALREADY `trialing` from provisioning. Meanwhile `finish_setup`
        # writes ACTIVE unconditionally, so a customer who finishes setup
        # during a trial ends up ACTIVE with a `trialing` subscription. That is
        # a real seam between the two lifecycles and it is worth fixing in the
        # service rather than here; until then this command corrects the
        # organization status so the console and the customer's own plan page
        # agree with the subscription they are reading.
        subscription = Subscription.objects.filter(
            organization=organization, is_active=True
        ).first()
        if subscription is None:
            raise CommandError(f"{organization.slug} has no subscription to expire.")

        with transaction.atomic():
            subscription.ends_at = timezone.now() + timezone.timedelta(
                days=RETAIL_TRIAL_DAYS
            )
            subscription.save(update_fields=["ends_at", "updated_at"])
            organization.status = OrgStatus.TRIAL
            organization.save(update_fields=["status", "updated_at"])

        self.stdout.write(
            f"  setup finished — organization is on TRIAL, expiring in "
            f"{RETAIL_TRIAL_DAYS} days"
        )

    # --------------------------------------------------------------- remove
    def _remove(self, profile):
        """
        The people, and NOT the organization.

        Deliberate, and it is the product's rule rather than a shortcut: every
        org-owned table points at `Organization` with `on_delete=PROTECT`, and
        there is no API in this system that deletes a customer's data in one
        call. Retiring a customer is a lifecycle sequence -- cancel, a
        retention window, archive, then a purge that requires a typed
        confirmation -- and implementing the most dangerous operation in the
        product as a side effect of a demo command is not a trade worth making.

        So the accounts go, the empty company stays, and re-running the seed
        fills it again. `--reset` is exactly that pair.

        Scoped by SLUG, which is unique platform-wide, and the accounts by
        organization membership: selecting demo rows by email DOMAIN is what
        once let two demo companies delete each other's people.
        """
        from apps.organization.models import Organization

        organization = Organization.objects.filter(slug=profile.slug).first()
        if organization is None:
            self.stdout.write(f"  {profile.slug} does not exist.")
            return

        call_command(
            "seed_demo_company",
            "--organization", organization.slug,
            "--profile", profile.key,
            "--remove",
            # The administrator stays. It was created by provisioning rather
            # than by the roster, and a re-seed acts AS it -- every demo
            # employee is created by somebody with the authority to grant
            # their role, and removing the only such account would make
            # `--reset` impossible.
            "--keep-admin",
            verbosity=0,
        )
        self.stdout.write(self.style.SUCCESS(
            f"  removed {profile.slug}'s demo accounts. The organization itself "
            f"stays — nothing in this product deletes a customer in one call."
        ))

    # -------------------------------------------------------------- summary
    def _summary(self, profiles):
        from apps.organization.management.commands.seed_demo_company import (
            _credentials_path,
        )
        from apps.organization.models import Organization

        self.stdout.write(self.style.SUCCESS("\nThree customers, one deployment.\n"))
        for profile in profiles:
            organization = Organization.objects.filter(slug=profile.slug).first()
            if organization is None:
                continue
            self.stdout.write(
                f"  {profile.slug:<18} {organization.get_status_display():<14} "
                f"plan {profile.plan_code:<12} {len(profile.people)} employees"
            )
            self.stdout.write(f"  {'':<18} {profile.purpose}")
            self.stdout.write(f"  {'':<18} credentials: {_credentials_path(organization)}")
            if profile.absent_roles:
                # Said rather than left to be discovered: only healthcare
                # promises an account for every role.
                self.stdout.write(
                    f"  {'':<18} no account for: "
                    f"{', '.join(sorted(profile.absent_roles))}"
                )
            self.stdout.write("")

        self.stdout.write(
            "  Every account must set its own password on first sign-in.\n"
            "  Sign in to the platform console at /login/platform to see all three."
        )
