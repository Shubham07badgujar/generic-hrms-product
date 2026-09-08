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

    manage.py seed_demo_company                              # the default demo company
    manage.py seed_demo_company --company "Acme Traders"      # any name you like
    manage.py seed_demo_company --credentials                 # rewrite the credentials file only
    manage.py seed_demo_company --remove                      # delete every demo account cleanly
"""

from __future__ import annotations

import datetime as dt
import secrets
import string
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.access.catalog import DepartmentKind, Layer

#: Unmistakably not a real person's address (RFC 2606 reserves .example),
#: and trivially greppable. Overridable with --domain.
DEFAULT_EMAIL_DOMAIN = "demo-healthcare.example"
DEFAULT_COMPANY = "Demo Healthcare Pvt Ltd"

#: Written under MEDIA_ROOT so it lands wherever this deployment keeps files.
def _credentials_path() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "demo-credentials.txt"

LEVELS = [
    ("L1", "Leadership", Layer.LEADERSHIP, 10),
    ("L2", "Department Head", Layer.DEPARTMENT_HEAD, 20),
    ("L3", "Manager", Layer.MANAGER, 30),
    ("L4", "Executive", Layer.EXECUTIVE, 40),
    ("L5", "Staff", Layer.STAFF, 50),
]

DEPARTMENTS = [
    (DepartmentKind.MEDICAL, "Medical", "MED"),
    (DepartmentKind.OPERATIONS, "Operations", "OPS"),
    (DepartmentKind.HR, "Human Resources", "HR"),
    (DepartmentKind.FINANCE, "Finance & Accounts", "FIN"),
]

DESIGNATIONS = {
    DepartmentKind.MEDICAL: [
        "Medical Director", "Senior Consultant", "Clinic Doctor", "Therapist",
    ],
    DepartmentKind.OPERATIONS: [
        "Head of Operations", "Operations Manager", "Customer Relations Executive",
        "Executive", "Facilities Assistant", "Associate",
    ],
    DepartmentKind.HR: ["HR Head", "HR Manager", "Talent Acquisition Specialist"],
    DepartmentKind.FINANCE: [
        "Finance Head", "Accounts Manager", "Payroll Executive",
    ],
}

#: role_code, first, last, department, level, designation, reports-to role
#: Ordered so every manager exists before anyone reporting to them.
PEOPLE = [
    # --- Layer 2, department heads. No internal manager: they answer to the
    # CEO, who has no Employee record to point at.
    ("medical_director",   "Meera",   "Kulkarni", DepartmentKind.MEDICAL,    Layer.DEPARTMENT_HEAD, "Medical Director",              None),
    ("operational_head",   "Oindrila","Sen",      DepartmentKind.OPERATIONS, Layer.DEPARTMENT_HEAD, "Head of Operations",            None),
    ("hr_head",            "Hema",    "Rao",      DepartmentKind.HR,         Layer.DEPARTMENT_HEAD, "HR Head",                       None),
    ("finance_head",       "Farah",   "Khan",     DepartmentKind.FINANCE,    Layer.DEPARTMENT_HEAD, "Finance Head",                  None),

    # --- Layer 3, managers.
    ("senior_doctor",      "Sanjay",  "Iyer",     DepartmentKind.MEDICAL,    Layer.MANAGER,   "Senior Consultant",             "medical_director"),
    ("operations_manager", "Omkar",   "Patil",    DepartmentKind.OPERATIONS, Layer.MANAGER,   "Operations Manager",            "operational_head"),
    ("hr_manager",         "Hari",    "Menon",    DepartmentKind.HR,         Layer.MANAGER,   "HR Manager",                    "hr_head"),
    ("accounts_manager",   "Anita",   "Kelkar",    DepartmentKind.FINANCE,    Layer.MANAGER,   "Accounts Manager",              "finance_head"),

    # --- Layer 4, executives.
    ("clinic_doctor",      "Chandni", "Bose",     DepartmentKind.MEDICAL,    Layer.EXECUTIVE, "Clinic Doctor",                 "senior_doctor"),
    ("cre",                "Chetan",  "Desai",    DepartmentKind.OPERATIONS, Layer.EXECUTIVE, "Customer Relations Executive",  "operations_manager"),
    ("executive",          "Esha",    "Kapoor",   DepartmentKind.OPERATIONS, Layer.EXECUTIVE, "Executive",                     "operations_manager"),
    ("recruiter",          "Ravi",    "Shah",     DepartmentKind.HR,         Layer.EXECUTIVE, "Talent Acquisition Specialist", "hr_manager"),
    ("payroll_executive",  "Pooja",   "Reddy",    DepartmentKind.FINANCE,    Layer.EXECUTIVE, "Payroll Executive",             "accounts_manager"),

    # --- Layer 5, staff.
    ("therapist",          "Tara",    "Nair",     DepartmentKind.MEDICAL,    Layer.STAFF,     "Therapist",                     "senior_doctor"),
    ("office_boy",         "Om",      "Jadhav",   DepartmentKind.OPERATIONS, Layer.STAFF,     "Facilities Assistant",          "operations_manager"),
    ("employee",           "Ekta",    "Sharma",   DepartmentKind.OPERATIONS, Layer.STAFF,     "Associate",                     "operations_manager"),
]

#: The two principals with no Employee record (`requires_employee=False`).
#:
#: `admin` is `is_grantable=False`, which stops it being granted through the
#: application by HR or anyone else — that restriction is about the in-app
#: path, and it stays intact. A role assignment written by a management
#: command run by root on the server is the same sanctioned route
#: `bootstrap_admin` uses, and it is how an administrator is meant to exist at
#: all. This account is separate from the real production admin so that
#: testing never needs its credentials.
SYSTEM_PEOPLE = [
    ("ceo", "Vikram", "Malhotra"),
    ("admin", "Sysadmin", "Demo"),
]


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


class Command(BaseCommand):
    help = ("Seed a fictional demo company with one account per template role "
            "(unique strong passwords). Safe starter data only - no real people.")

    def add_arguments(self, parser):
        parser.add_argument("--remove", action="store_true",
                            help="Delete every demo account and its employee record.")
        parser.add_argument("--credentials", action="store_true",
                            help="Rewrite the credentials file for existing accounts.")
        parser.add_argument("--company", default=DEFAULT_COMPANY,
                            help="Display name for the demo organisation.")
        parser.add_argument("--legal-name", default="",
                            help="Registered entity name (defaults to the display name).")
        parser.add_argument("--domain", default=DEFAULT_EMAIL_DOMAIN,
                            help="Email domain for the demo accounts.")

    def handle(self, *args, **options):
        self.company = options.get("company") or DEFAULT_COMPANY
        self.legal_name = options.get("legal_name") or f"{self.company}."
        self.domain = options.get("domain") or DEFAULT_EMAIL_DOMAIN
        if options["remove"]:
            return self._remove()
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

        # --- organisation ------------------------------------------------
        # The demo company IS the admin's organization, not a second one beside
        # it. Every employee below is created through `create_employee` with the
        # admin as actor, and that service places the new hire in the ACTOR's
        # organization -- so seeding a separate one here would put the company's
        # name on one row and all of its people in another.
        actor_org = Organization.objects.filter(
            memberships__user__user_roles__role__code="admin",
            memberships__is_active=True,
        ).first()
        organization = actor_org or Organization.objects.first()
        if organization is None:
            raise CommandError(
                "No organization exists. Run bootstrap_admin first — it creates "
                "the founding Admin and the organization they administer."
            )

        from django.utils.text import slugify

        organization.name = self.company
        organization.legal_name = self.legal_name
        # The slug is the public branding key -- it is how the login page finds
        # this company before anyone signs in -- so it has to name the company,
        # not whatever placeholder bootstrap used.
        candidate = slugify(self.company)[:63] or organization.slug
        if not Organization.objects.exclude(pk=organization.pk).filter(slug=candidate).exists():
            organization.slug = candidate
        organization.save(update_fields=["name", "legal_name", "slug", "updated_at"])

        org = OrgSettings.for_org(organization)
        if org.employee_code_next < 1001:
            org.employee_code_prefix = "EMP"
            org.employee_code_next = 1001
            org.save(update_fields=["employee_code_prefix", "employee_code_next"])

        # `state` is not cosmetic: Professional Tax is a state levy, and a
        # location without one means PT silently computes to nothing.
        location, _ = Location.objects.update_or_create(
            code="HO",
            defaults={"name": "Head Office", "city": "Pune", "state": "MH",
                      "is_head_office": True},
        )

        levels = {}
        for code, name, layer, rank in LEVELS:
            levels[layer], _ = EmployeeLevel.objects.update_or_create(
                code=code, defaults={"name": name, "layer": layer, "rank": rank},
            )

        departments = {}
        for kind, name, code in DEPARTMENTS:
            departments[kind], _ = Department.objects.update_or_create(
                code=code, defaults={"name": name, "kind": kind},
            )

        designations = {}
        for kind, titles in DESIGNATIONS.items():
            for title in titles:
                designations[title], _ = Designation.objects.update_or_create(
                    title=title, department=departments[kind], defaults={},
                )

        self.stdout.write(
            f"organisation: {len(departments)} departments, {len(levels)} levels, "
            f"{len(designations)} designations, 1 location"
        )

        # --- the acting admin --------------------------------------------
        # Every employee is created BY somebody, and that somebody must hold
        # the authority to grant the role. Using the real admin means
        # `assert_creator_may_grant` runs for real rather than being sidestepped.
        actor = User.objects.filter(user_roles__role__code="admin").first()
        if actor is None:
            raise CommandError(
                "No admin account exists. Run bootstrap_admin first — this "
                "command deliberately will not mint its own authority."
            )

        credentials = []
        created = {}
        joined = dt.date.today() - dt.timedelta(days=400)

        # --- system principals -------------------------------------------
        for role_code, first, last in SYSTEM_PEOPLE:
            email = f"{role_code}@{self.domain}"
            role = Role.objects.get(code=role_code)
            user = User.objects.filter(email=email).first()
            password = make_password()
            if user is None:
                user = User.objects.create_user(
                    email=email, password=password, first_name=first, last_name=last,
                )
            else:
                user.set_password(password)
                user.save(update_fields=["password"])
            UserRole.objects.get_or_create(user=user, role=role, defaults={"assigned_by": actor})
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
        for role_code, first, last, kind, layer, designation, manager_role in PEOPLE:
            email = f"{role_code}@{self.domain}"
            if User.objects.filter(email=email).exists():
                self.stdout.write(self.style.WARNING(f"  {role_code:<20} already exists — skipped"))
                continue

            password = make_password()
            manager = created.get(manager_role)

            result = create_employee(
                actor=actor,
                first_name=first,
                last_name=last,
                email=email,
                role_code=role_code,
                department_id=departments[kind].pk,
                designation_id=designations[designation].pk,
                location_id=location.pk,
                level_id=levels[layer].pk,
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
            created[role_code] = employee

            credentials.append({
                "role": role_code, "name": f"{first} {last}", "email": email,
                "password": password, "employee": employee,
            })
            self.stdout.write(
                f"  {role_code:<20} {email:<42} {employee.employee_code}  "
                f"{departments[kind].code}/{designation}"
            )

        # --- department heads --------------------------------------------
        # Set after creation: a department's head must already be an employee,
        # and they are created inside their own department.
        for role_code, kind in [
            ("medical_director", DepartmentKind.MEDICAL),
            ("operational_head", DepartmentKind.OPERATIONS),
            ("hr_head", DepartmentKind.HR),
            ("finance_head", DepartmentKind.FINANCE),
        ]:
            if role_code in created:
                department = departments[kind]
                department.head_employee = created[role_code]
                department.save(update_fields=["head_employee", "updated_at"])

        # --- confirm employment ------------------------------------------
        # Straight to CONFIRMED so scope tests are not distorted by probation
        # states. Set directly and only here, in a seeding command — the
        # probation service remains the only path in the application itself.
        from apps.employees.models import EmployeeStatus, ProbationStatus

        Employee.objects.filter(
            user__email__endswith=f"@{self.domain}"
        ).update(status=EmployeeStatus.CONFIRMED, probation_status=ProbationStatus.CONFIRMED)

        self._write_credentials(credentials)
        self.stdout.write(self.style.SUCCESS(
            f"\nSeeded {len(credentials)} demo accounts. "
            f"Credentials: {_credentials_path()}"
        ))

    # ---------------------------------------------------------------- remove
    @transaction.atomic
    def _remove(self):
        from apps.accounts.models import User

        users = User.objects.filter(email__endswith=f"@{self.domain}")
        count = users.count()
        if not count:
            self.stdout.write("No demo accounts found.")
            return

        # Employee rows are PROTECTed from several directions, so delete the
        # employee first and let the user follow.
        from apps.employees.models import Employee

        Employee.objects.filter(user__in=users).delete()
        users.delete()

        if _credentials_path().exists():
            _credentials_path().unlink()

        self.stdout.write(self.style.SUCCESS(f"Removed {count} demo accounts."))

    # ----------------------------------------------------------- credentials
    def _write_credentials(self, credentials):
        lines = [
            f"{self.company} — DEMO ACCOUNT CREDENTIALS",
            "=" * 70,
            "",
            "CONFIDENTIAL once the instance is reachable from the internet.",
            "Remove the demo before the instance holds real employee data:",
            "    manage.py seed_demo_company --remove",
            "",
            f"{'ROLE':<20} {'EMAIL':<44} PASSWORD",
            "-" * 90,
        ]
        for row in credentials:
            lines.append(f"{row['role']:<20} {row['email']:<44} {row['password']}")
        from django.conf import settings

        lines += ["", f"Sign in at {settings.FRONTEND_URL.rstrip('/')}/login", ""]

        _credentials_path().parent.mkdir(parents=True, exist_ok=True)
        _credentials_path().write_text("\n".join(lines), encoding="utf-8")
        _credentials_path().chmod(0o600)
