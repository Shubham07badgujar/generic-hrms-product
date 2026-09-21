"""
Seed a complete demo organisation with one working account per role.

DIFFERENT FROM `seed_demo`, deliberately.

`seed_demo` gives every account the SAME well-known password and refuses to run
with DEBUG off, which is correct — that shape must never reach a deployment
reachable from the internet. This command exists because the need is real
(somebody has to test all eighteen roles on the deployed instance) and the
honest answer is not "use the unsafe one with --force":

  * every account gets its OWN randomly generated 20-character password
  * passwords are written to a 0600 file on the server and printed nowhere else
  * every employee is created through `create_employee`, so the hierarchy,
    department, level and manager rules all apply exactly as they would to a
    real hire — nothing here bypasses a validation or a permission check
  * accounts use an unmistakably fictional email domain and are removable in one
    command, because demo people in a live HR database show up in headcount,
    BI and payroll runs

Two of the eighteen roles have no Employee record by design: `admin` and `ceo`
are system principals (`requires_employee=False`). That is the architecture,
not an omission, and the documentation says so.

    manage.py seed_demo_company                              # the only organization
    manage.py seed_demo_company --organization acme          # a named one
    manage.py seed_demo_company --company "Acme Traders"      # any display name you like
    manage.py seed_demo_company --remove                      # delete this org's demo accounts

ONE ORGANIZATION, NAMED
-----------------------
Written for a single-company database, this command did real damage in a
multi-customer one:

  * It chose "an organization that has an admin", else the first organization --
    and then RENAMED it. With several customers that renames one of them.
  * It took "the first admin on the platform" as the actor, and `create_employee`
    places a new hire in the ACTOR's organization, so the demo people could land
    in a different company from the structure built for them.
  * It upserted departments, levels and locations by code, and those codes are
    unique per organization, so it rewrote another company's rows.
  * `Role.objects.get(code=...)` raised MultipleObjectsReturned outright once a
    second organization had its roles seeded.
  * Confirming employment and `--remove` both selected by email DOMAIN across
    every organization.
  * Every organization wrote the same credentials file, so one company's demo
    passwords overwrote another's.

Now it inherits `OrganizationCommand`, scopes every lookup to that organization,
acts as that organization's own admin, confines removal to that organization's
members, and keeps the credentials file inside that organization's media subtree.
"""

from __future__ import annotations

import datetime as dt
import secrets
import string
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.organization.demo import LEVELS, PROFILES, get_profile
from core.management.orgcommand import OrganizationCommand
from core.models import org_scoped

#: Seeded when no profile is named, so `manage.py seed_demo_company` with no
#: arguments still builds the complete-coverage healthcare company it always
#: did. The constants it used to hold inline are now one profile among three
#: in `apps/organization/demo/profiles.py` -- a second and a third company are
#: what make a multi-tenant claim checkable, and they cannot share one set of
#: module-level constants.
DEFAULT_PROFILE = "healthcare"


def _credentials_path(organization) -> Path:
    """
    Under THIS organization's media subtree.

    It was one file for the whole deployment, so seeding a second organization's
    demo overwrote the first one's passwords. Every other tenant file already
    lives under `organizations/<uuid>/`, and this one belongs there for the same
    reason.
    """
    from django.conf import settings

    return (
        Path(settings.MEDIA_ROOT)
        / "organizations"
        / str(organization.pk)
        / "demo-credentials.txt"
    )


def make_password() -> str:
    """
    20 characters from a set with no ambiguous glyphs.

    Long enough that the 12-character policy floor is met with room to spare,
    and unique per account so one leaked credential is one account.
    """
    alphabet = (
        string.ascii_lowercase.replace("l", "").replace("o", "")
        + string.ascii_uppercase.replace("I", "").replace("O", "")
        + "23456789"
        + "!@#$%^&*-_=+"
    )
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(20))
        # Guarantee the mix, rather than trusting 20 random draws to contain it.
        if (any(c.islower() for c in candidate)
                and any(c.isupper() for c in candidate)
                and any(c.isdigit() for c in candidate)
                and any(not c.isalnum() for c in candidate)):
            return candidate


class Command(OrganizationCommand):
    help = ("Seed a fictional demo company with one account per template role "
            "(unique strong passwords). Safe starter data only - no real people.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--remove", action="store_true",
                            help="Delete this organization's demo accounts and their employee records.")
        parser.add_argument(
            "--keep-admin", action="store_true",
            help=(
                "With --remove, spare the account holding this organization's "
                "Admin grant. For an organization that outlives its demo people."
            ),
        )
        parser.add_argument("--profile", default=DEFAULT_PROFILE,
                            choices=sorted(PROFILES),
                            help="Which fictional company to build.")
        parser.add_argument("--company", default="",
                            help="Display name (defaults to the profile's).")
        parser.add_argument("--legal-name", default="",
                            help="Registered entity name (defaults to the display name).")
        parser.add_argument("--domain", default="",
                            help="Email domain for the demo accounts (defaults to the profile's).")

    def handle_for_organization(self, organization, *args, **options):
        self.organization = organization
        self.profile = get_profile(options.get("profile") or DEFAULT_PROFILE)
        # Each overridable, and each defaulting to the profile rather than to a
        # module constant: two profiles sharing a domain would make `--remove`
        # ambiguous, and a caller who overrides one has said which they mean.
        self.company = options.get("company") or self.profile.company
        self.legal_name = (
            options.get("legal_name")
            or (self.profile.legal_name if not options.get("company") else "")
            or f"{self.company}."
        )
        self.domain = options.get("domain") or self.profile.domain
        # The slug the organization should answer to afterwards. The PROFILE's
        # when it is building a profile as written, and derived from the name
        # only when a caller overrode it -- because a profile is provisioned
        # under its own slug and re-slugging it from the display name would
        # move the company out from under the command that just created it,
        # and out from under every `--remove` and every link to it.
        self.slug_hint = (
            slugify(options["company"])[:63]
            if options.get("company")
            else self.profile.slug
        )
        if options["remove"]:
            return self._remove(keep_admin=options.get("keep_admin", False))

        # Refused before the first write, not discovered eleven employees in.
        # Everything `validate()` checks is a rule `create_employee` enforces,
        # and a profile that breaks one would roll back a transaction that had
        # already done most of the work.
        problems = self.profile.validate()
        if problems:
            raise CommandError(
                "This demo profile is malformed:\n  "
                + "\n  ".join(problems)
            )
        return self._seed()

    # ------------------------------------------------------------------ seed
    @transaction.atomic
    def _seed(self):
        from apps.accounts.models import Role, User, UserRole
        from apps.employees.models import Employee
        from apps.employees.services.creation import create_employee
        from apps.organization.models import (
            Department, Designation, EmployeeLevel, Location, Organization,
            OrganizationMembership, OrgSettings,
        )

        organization = self.organization

        # --- the acting admin --------------------------------------------
        # THIS organization's admin, and resolved first. `create_employee`
        # places a new hire in the actor's organization, so an admin from any
        # other company would put every demo person there instead -- which is
        # what "the first admin on the platform" did. Using a real admin also
        # means `assert_creator_may_grant` runs rather than being sidestepped.
        admin_grant = (
            org_scoped(UserRole, organization)
            .filter(role__code="admin", is_active=True)
            .select_related("user")
            .first()
        )
        if admin_grant is None:
            raise CommandError(
                f"{organization.slug} has no admin account. Run bootstrap_admin, "
                f"or provision the organization -- this command deliberately will "
                f"not mint its own authority."
            )
        actor = admin_grant.user

        # --- organisation ------------------------------------------------
        organization.name = self.company
        organization.legal_name = self.legal_name
        # The slug is the public branding key -- it is how the login page finds
        # this company before anyone signs in -- so it has to name the company,
        # not whatever placeholder bootstrap used.
        candidate = self.slug_hint or organization.slug
        if not Organization.objects.exclude(pk=organization.pk).filter(slug=candidate).exists():
            organization.slug = candidate
        organization.save(update_fields=["name", "legal_name", "slug", "updated_at"])

        org = OrgSettings.for_org(organization)
        if org.employee_code_next < 1001:
            org.employee_code_prefix = "EMP"
            org.employee_code_next = 1001
            org.save(update_fields=["employee_code_prefix", "employee_code_next"])

        profile = self.profile

        # `state` is not cosmetic: Professional Tax is a state levy, and a
        # location without one means PT silently computes to nothing. More than
        # one site per company on purpose, too: a single-location demo cannot
        # exercise a holiday calendar that differs by state, and two of these
        # companies genuinely operate across two.
        locations = {}
        for site in profile.locations:
            locations[site.code], _ = org_scoped(Location, organization).update_or_create(
                code=site.code,
                defaults={"name": site.name, "city": site.city, "state": site.state,
                          "is_head_office": site.is_head_office},
            )
        head_office = locations[profile.head_office.code]

        levels = {}
        for code, name, layer, rank in LEVELS:
            levels[layer], _ = org_scoped(EmployeeLevel, organization).update_or_create(
                code=code, defaults={"name": name, "layer": layer, "rank": rank},
            )

        # Keyed by department CODE rather than kind: a profile may hold two
        # departments of the same kind, and keying by kind silently merged
        # them into one. Technology has exactly that shape.
        departments = {}
        for entry in profile.departments:
            departments[entry.code], _ = org_scoped(Department, organization).update_or_create(
                code=entry.code, defaults={"name": entry.name, "kind": entry.kind},
            )

        # Keyed by (department, title), because two departments may carry the
        # same job title and a title-only key hands the second one the first
        # department's row.
        designations = {}
        for entry in profile.departments:
            for title in entry.designations:
                designations[(entry.code, title)], _ = org_scoped(
                    Designation, organization
                ).update_or_create(
                    title=title, department=departments[entry.code], defaults={},
                )

        # A rename, not a redefinition: the matrix, the layers and the
        # segregation-of-duties rules are code and identical in every company.
        # This is the product's own claim that the seeded roles are defaults
        # the customer owns, exercised rather than asserted.
        for code, name in profile.role_names.items():
            org_scoped(Role, organization).filter(code=code).update(name=name)

        self.stdout.write(
            f"organisation: {len(departments)} departments, {len(levels)} levels, "
            f"{len(designations)} designations, {len(locations)} location(s)"
        )

        credentials = []
        created = {}
        joined = dt.date.today() - dt.timedelta(days=400)
        roles = org_scoped(Role, organization)

        # --- system principals -------------------------------------------
        for role_code, first, last in profile.system_people:
            email = f"{role_code}@{self.domain}"
            role = roles.get(code=role_code)
            user = User.objects.filter(email=email).first()
            password = make_password()
            if user is None:
                user = User.objects.create_user(
                    email=email, password=password, first_name=first, last_name=last,
                )
            else:
                user.set_password(password)
                user.save(update_fields=["password"])
            org_scoped(UserRole, organization).get_or_create(
                user=user, role=role, defaults={"assigned_by": actor}
            )
            # These two are the only accounts this command builds WITHOUT going
            # through `create_employee`, because admin and ceo are system
            # principals with no Employee record -- so they are also the only
            # ones that do not get their membership from that service. Without
            # it they resolve to DENY_ALL, and this command's promise of "one
            # working account per role" would be false for exactly the two
            # roles that can reach everything.
            OrganizationMembership.objects.get_or_create(
                organization=organization, user=user
            )
            credentials.append({
                "role": role_code, "name": f"{first} {last}", "email": email,
                "password": password, "employee": None,
            })
            self.stdout.write(f"  {role_code:<20} {email}  (system principal, no employee record)")

        # --- employees ----------------------------------------------------
        for person in profile.people:
            email = f"{person.username}@{self.domain}"
            if User.objects.filter(email=email).exists():
                self.stdout.write(
                    self.style.WARNING(f"  {person.username:<20} already exists — skipped")
                )
                continue

            password = make_password()
            manager = created.get(person.manager)
            site = locations[person.location] if person.location else head_office

            result = create_employee(
                actor=actor,
                first_name=person.first_name,
                last_name=person.last_name,
                email=email,
                role_code=person.role_code,
                department_id=departments[person.department].pk,
                designation_id=designations[(person.department, person.designation)].pk,
                location_id=site.pk,
                level_id=levels[person.level].pk,
                reporting_manager_id=manager.pk if manager else None,
                date_of_joining=joined,
                temporary_password=password,
                phone="",
                # Confirmed staff: a demo organisation full of people stuck in
                # onboarding cannot exercise the workflows being tested.
                probation_months=None,
                start_onboarding_checklist=False,
            )
            employee = result.employee
            created[person.username] = employee

            credentials.append({
                "role": person.role_code,
                "name": f"{person.first_name} {person.last_name}",
                "email": email,
                "password": password, "employee": employee,
            })
            self.stdout.write(
                f"  {person.username:<20} {email:<42} {employee.employee_code}  "
                f"{person.department}/{person.designation}"
            )

        # --- department heads --------------------------------------------
        # Set after creation: a department's head must already be an employee.
        # A department may legitimately have none, so the profile names its
        # head rather than this loop assuming every department has one.
        for entry in profile.departments:
            if entry.head and entry.head in created:
                department = departments[entry.code]
                department.head_employee = created[entry.head]
                department.save(update_fields=["head_employee", "updated_at"])

        # --- confirm employment ------------------------------------------
        # Straight to CONFIRMED so scope tests are not distorted by probation
        # states. Set directly and only here, in a seeding command — the
        # probation service remains the only path in the application itself.
        #
        # Scoped to THIS organization as well as the demo domain. Selecting by
        # domain alone confirmed every matching employee in every organization.
        from apps.employees.models import EmployeeStatus, ProbationStatus

        org_scoped(Employee, organization).filter(
            user__email__endswith=f"@{self.domain}"
        ).update(status=EmployeeStatus.CONFIRMED, probation_status=ProbationStatus.CONFIRMED)

        self._write_credentials(credentials)
        self.stdout.write(self.style.SUCCESS(
            f"\nSeeded {len(credentials)} demo accounts. "
            f"Credentials: {_credentials_path(organization)}"
        ))

    # ---------------------------------------------------------------- remove
    @transaction.atomic
    def _remove(self, *, keep_admin=False):
        from apps.accounts.models import User
        from apps.employees.models import Employee

        organization = self.organization

        # Members of THIS organization on the demo domain, and nobody else.
        # Selecting by domain alone deleted any matching account in any
        # organization -- a real employee somewhere else whose address happened
        # to share the domain would have gone with the demo.
        users = User.objects.filter(
            email__endswith=f"@{self.domain}",
            memberships__organization=organization,
        ).distinct()

        if keep_admin:
            # The organization's administrator is not part of the demo roster:
            # it was created when the organization was provisioned, and an
            # organization that outlives its demo people still needs somebody
            # who can administer it -- including somebody for a re-seed to act
            # as, since every demo employee is created BY an admin.
            # `org_scoped` is the module-level import. Importing it again here
            # would make it a LOCAL name for the whole function, and the plain
            # `--remove` path below would then raise UnboundLocalError.
            from apps.accounts.models import UserRole

            spared = set(
                org_scoped(UserRole, organization)
                .filter(role__code="admin", is_active=True)
                .values_list("user_id", flat=True)
            )
            users = users.exclude(pk__in=spared)
        count = users.count()
        if not count:
            self.stdout.write(f"No demo accounts found in {organization.slug}.")
            return

        # Employee rows are PROTECTed from several directions, so delete the
        # employee first and let the user follow.
        org_scoped(Employee, organization).filter(user__in=users).delete()
        User.objects.filter(pk__in=list(users.values_list("pk", flat=True))).delete()

        path = _credentials_path(organization)
        if path.exists():
            path.unlink()

        self.stdout.write(self.style.SUCCESS(
            f"Removed {count} demo accounts from {organization.slug}."
        ))

    # ----------------------------------------------------------- credentials
    def _write_credentials(self, credentials):
        lines = [
            f"{self.company} — DEMO ACCOUNT CREDENTIALS",
            "=" * 70,
            "",
            "CONFIDENTIAL once the instance is reachable from the internet.",
            "Remove the demo before the instance holds real employee data:",
            f"    manage.py seed_demo_company --organization {self.organization.slug} --remove",
            "",
            f"{'ROLE':<20} {'EMAIL':<44} PASSWORD",
            "-" * 90,
        ]
        for row in credentials:
            lines.append(f"{row['role']:<20} {row['email']:<44} {row['password']}")
        from django.conf import settings

        lines += ["", f"Sign in at {settings.FRONTEND_URL.rstrip('/')}/login", ""]

        path = _credentials_path(self.organization)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        path.chmod(0o600)
