"""
One command that turns an empty database into a system you can test by hand.

Runs every configuration seed in dependency order, creates a fictional demo company with one signed-in-able account per role, and then lays down enough
TRANSACTIONAL data that no screen is empty: salary structures, a month of
attendance, leave requests awaiting a decision, assets in people's hands, a
recruitment pipeline with candidates at different stages, and one new joiner
still behind the onboarding gate.

    manage.py seed_all                       # the default demo company
    manage.py seed_all --company "Acme Ltd"  # any name
    manage.py seed_all --reset               # wipe the demo and rebuild it

Everything it creates is fictional. It refuses to run on a database that
already holds real-looking employees unless you pass --force, because the
one thing worse than an empty test system is a seeded production one.

What it deliberately does NOT do:

  * Verify the statutory rate sets. They arrive as DRAFTS, exactly as they
    would for a real company, so that certifying them is one of the things
    you test (Payroll -> Settings, as the Finance Head).
  * Approve the payroll run it creates. It stops at "review", which is where
    a preparer leaves it; approving it is the four-eyes step you test.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.platform.services.provisioning import CONFIG_SEEDS
from core.middleware import acting_as

#: Configuration seeds come from the PROVISIONING SERVICE, not from a list kept
#: here.
#:
#: They used to be a list in this file, which is a developer convenience
#: command. A real customer, created through the platform, would then have
#: silently lacked whatever somebody added here and not there -- and the
#: absence would surface months later as "why does this company have no exit
#: clearance template". One statement of what an organization needs, imported
#: by both.
#:
#: `seed_statutory` is not in it and is run separately below: India's PF, ESI
#: and Professional Tax tables are facts about the Republic of India, seeded
#: once per deployment, not once per customer.


class Command(BaseCommand):
    help = (
        "Empty database -> a fully populated demo system for manual testing. "
        "Runs every seed, then adds payroll, attendance, leave, asset and "
        "recruitment data so no screen is blank."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", default="Demo Healthcare Pvt Ltd")
        parser.add_argument("--legal-name", default="")
        parser.add_argument("--domain", default="demo-healthcare.example")
        parser.add_argument(
            "--reset", action="store_true",
            help="Remove the existing demo accounts first, then rebuild.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Proceed even if the database already holds employees.",
        )
        parser.add_argument(
            "--skip-transactions", action="store_true",
            help="Configuration and people only — no payroll/leave/asset data.",
        )

    # ------------------------------------------------------------------ main
    def handle(self, *args, **options):
        self.domain = options["domain"]
        self.company = options["company"]

        from apps.employees.models import Employee

        if options["reset"]:
            self.stdout.write(self.style.WARNING("Removing the existing demo…"))
            call_command("seed_demo_company", "--remove", "--domain", self.domain)

        outsiders = (
            Employee.objects.filter(is_active=True)
            .exclude(user__email__endswith=f"@{self.domain}")
            .count()
        )
        if outsiders and not options["force"]:
            raise CommandError(
                f"This database already holds {outsiders} employee(s) that are not "
                f"demo accounts. Seeding demo data into a real system is almost "
                f"never what you want. Re-run with --force if it is."
            )

        self._banner("1/3  Configuration")
        # The organization has to exist and be BOUND before any of this runs.
        # Org-owned rows take their organization from the acting context, so
        # without it the first `LeaveType` raises `OrgContextMissing` -- which
        # is exactly what this command did after the tenancy conversion, on
        # every run, undetected because no test drives a seed command.
        organization = self._ensure_organization(options)
        call_command("seed_statutory", verbosity=0)
        self.stdout.write(f"  {'statutory':<20} rate sets (drafts, for you to certify)")
        with acting_as(None, organization=organization):
            for _key, what, seed in CONFIG_SEEDS:
                self.stdout.write(f"  {_key:<20} {what}")
                seed(organization)

        self._banner("2/3  Company and people")
        self._ensure_bootstrap_admin()
        call_command(
            "seed_demo_company",
            "--company", self.company,
            "--legal-name", options["legal_name"] or f"{self.company}.",
            "--domain", self.domain,
            verbosity=0,
        )
        people = self._people()
        self.stdout.write(f"  {len(people)} accounts, one per role")

        if options["skip_transactions"]:
            self.stdout.write(self.style.WARNING("\nSkipping transactional data."))
            return self._finish()

        self._banner("3/3  Data to look at")
        self._seed_transactions(people)
        self._finish()

    # ---------------------------------------------------------------- pieces
    def _ensure_organization(self, options):
        """
        The organization this demo lives in, adopted or created.

        Adopts the sole existing one -- the single-company self-hosted case --
        and otherwise creates one for the demo. Refuses to choose between
        several, for the same reason every other org-scoped command does:
        seeding one customer's configuration into another's account is silent.
        """
        from apps.organization.models import Organization, OrgStatus

        existing = list(Organization.objects.all()[:2])
        if len(existing) == 1:
            return existing[0]
        if existing:
            raise CommandError(
                "Several organizations exist, so there is no single one to "
                "seed the demo into. Provision a dedicated organization and "
                "seed it explicitly."
            )
        return Organization.objects.create(
            name=self.company,
            legal_name=options["legal_name"] or f"{self.company}.",
            slug=slugify(self.company) or "demo",
            status=OrgStatus.ACTIVE,
        )

    def _banner(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{text}"))

    def _people(self) -> dict:
        """The demo employees, keyed by the role code that made them."""
        from apps.employees.models import Employee

        found = {}
        for employee in Employee.objects.filter(
            is_active=True, user__email__endswith=f"@{self.domain}"
        ).select_related("user", "department", "location"):
            code = employee.user.email.split("@")[0]
            found[code] = employee
        return found

    def _ensure_bootstrap_admin(self):
        """
        seed_demo_company needs an admin to act AS — every employee is created
        by somebody with the authority to grant their role, and using a real
        admin means that check runs for real rather than being sidestepped.
        """
        from apps.accounts.models import User

        if User.objects.filter(user_roles__role__code="admin", is_active=True).exists():
            return
        call_command(
            "bootstrap_admin", "--email", f"setup@{self.domain}",
            "--first-name", "Setup", "--last-name", "Account", verbosity=0,
        )
        self.stdout.write(
            "  setup account created (no password — the demo admin below is the one to use)"
        )

    # ------------------------------------------------------- transactions
    @transaction.atomic
    def _seed_transactions(self, people):
        self._salaries(people)
        self._attendance(people)
        self._leave(people)
        self._assets(people)
        self._recruitment(people)
        self._joiner(people)

    # -- payroll ----------------------------------------------------------
    def _salaries(self, people):
        from apps.payroll.models import CalcType, ComponentType, SalaryComponent
        from apps.payroll.services.structures import create_salary_structure

        admin = people.get("admin") or next(iter(people.values()))
        actor = self._actor(people)

        catalogue = [
            ("BASIC", "Basic", CalcType.FIXED, True, None, 1),
            ("HRA", "House Rent Allowance", CalcType.PERCENT_OF, False, "BASIC", 2),
            ("CONVEYANCE", "Conveyance Allowance", CalcType.FIXED, False, None, 3),
            ("SPECIAL", "Special Allowance", CalcType.FIXED, False, None, 4),
        ]
        components = {}
        for code, name, calc, is_wage, base, order in catalogue:
            components[code], _ = SalaryComponent.objects.update_or_create(
                code=code,
                defaults={
                    "name": name, "component_type": ComponentType.EARNING,
                    "calc_type": calc, "percent_of_code": base or "",
                    "is_taxable": True, "is_part_of_ctc": True, "is_wage": is_wage,
                    "display_order": order,
                },
            )

        # A spread of salaries, so PF/ESI/PT thresholds all get exercised:
        # one below the ESI ceiling, one above it, one comfortably taxable.
        plan = [("therapist", 240000), ("cre", 420000), ("hr_manager", 900000)]
        made = 0
        for role_code, ctc in plan:
            employee = people.get(role_code)
            if employee is None:
                continue
            basic = Decimal(ctc) / Decimal(12) * Decimal("0.5")
            create_salary_structure(
                actor=actor, employee=employee, ctc_annual=Decimal(ctc),
                valid_from=dt.date(dt.date.today().year, 1, 1),
                lines=[
                    {"component": components["BASIC"].pk, "value": basic.quantize(Decimal("1"))},
                    {"component": components["HRA"].pk, "value": Decimal("40")},
                    {"component": components["CONVEYANCE"].pk, "value": Decimal("1600")},
                ],
            )
            made += 1
        self.stdout.write(f"  salary structures  {made} employees, {len(components)} components")

    # -- attendance -------------------------------------------------------
    def _attendance(self, people):
        from apps.attendance.models import (
            AttendanceRecord, EsslEmployeeLink, RecordSource, RecordStatus,
        )

        today = dt.date.today()
        start = today.replace(day=1)
        targets = [people[c] for c in ("therapist", "cre", "hr_manager") if c in people]
        days = records = 0

        for index, employee in enumerate(targets):
            EsslEmployeeLink.objects.get_or_create(
                essl_user_id=f"DEMO{1000 + index}",
                defaults={"employee": employee, "location": employee.location},
            )
            day = start
            while day <= today:
                if day.weekday() == 6:                      # Sunday: week off
                    day += dt.timedelta(days=1)
                    continue
                # One absence and one late per person, so the exceptions
                # queue and the monthly calendar both have something in them.
                if day.day == 9 + index:
                    status, first_in, worked, late = RecordStatus.ABSENT, None, 0, False
                else:
                    late = day.day == 5 + index
                    first_in = timezone.make_aware(
                        dt.datetime.combine(day, dt.time(10, 35 if late else 0))
                    )
                    status, worked = RecordStatus.PRESENT, 540
                _, created = AttendanceRecord.objects.get_or_create(
                    employee=employee, date=day,
                    defaults={
                        "status": status, "source": RecordSource.DEVICE,
                        "first_in": first_in,
                        "last_out": first_in + dt.timedelta(minutes=worked) if first_in else None,
                        "worked_minutes": worked, "is_late": late,
                        "late_minutes": 35 if late else 0,
                    },
                )
                records += int(created)
                days += 1
                day += dt.timedelta(days=1)
        self.stdout.write(f"  attendance         {records} days across {len(targets)} employees")

    # -- leave ------------------------------------------------------------
    def _leave(self, people):
        from apps.leave.models import LeaveRequest, LeaveStatus, LeaveType
        from apps.leave.services import ensure_balances

        paid = LeaveType.objects.filter(is_paid=True).order_by("name").first()
        if paid is None:
            return
        today = dt.date.today()
        made = 0
        for employee in people.values():
            ensure_balances(employee)

        # One awaiting a decision (so the approval queue is not empty), one
        # already approved (so a balance shows movement and payroll has an
        # unpaid day to reason about).
        plan = [
            ("cre", LeaveStatus.PENDING, 14, "Family function"),
            ("therapist", LeaveStatus.APPROVED, -7, "Medical rest"),
        ]
        for role_code, status, offset, reason in plan:
            employee = people.get(role_code)
            if employee is None:
                continue
            start = today + dt.timedelta(days=offset)
            _, created = LeaveRequest.objects.get_or_create(
                employee=employee, start_date=start,
                defaults={
                    "leave_type": paid, "end_date": start, "days": Decimal("1.0"),
                    "reason": reason, "status": status,
                },
            )
            made += int(created)
        self.stdout.write(f"  leave              {made} requests (one pending a decision)")

    # -- assets -----------------------------------------------------------
    def _assets(self, people):
        from apps.assets.models import (
            AllocationStatus, Asset, AssetAllocation, AssetCategory, AssetStatus,
        )

        category = AssetCategory.objects.order_by("name").first()
        if category is None:
            return
        location = next((e.location for e in people.values() if e.location_id), None)

        catalogue = [
            ("AST-0001", "Laptop 14in", "SN-DEMO-0001"),
            ("AST-0002", "Access Card", ""),
            ("AST-0003", "Desk Phone", "SN-DEMO-0003"),
        ]
        assets = []
        for tag, name, serial in catalogue:
            asset, _ = Asset.objects.update_or_create(
                asset_tag=tag,
                defaults={
                    "category": category, "name": name, "serial_number": serial,
                    "location": location, "status": AssetStatus.AVAILABLE,
                },
            )
            assets.append(asset)

        holder = people.get("cre") or next(iter(people.values()))
        allocated = 0
        if not AssetAllocation.objects.filter(
            asset=assets[0], status=AllocationStatus.ACTIVE
        ).exists():
            AssetAllocation.objects.create(
                asset=assets[0], employee=holder, allocated_at=timezone.now(),
                status=AllocationStatus.ACTIVE,
            )
            assets[0].status = AssetStatus.ALLOCATED
            assets[0].save(update_fields=["status"])
            allocated = 1
        self.stdout.write(
            f"  assets             {len(assets)} in the register, {allocated} in someone's hands"
        )

    # -- recruitment ------------------------------------------------------
    def _recruitment(self, people):
        from apps.recruitment.models import (
            Application, Candidate, JobOpening, JobStatus,
        )
        from apps.workflows.models import HiringWorkflow

        workflow = HiringWorkflow.objects.filter(is_active=True).first()
        recruiter = people.get("recruiter")
        hr_head = people.get("hr_head")
        if workflow is None or recruiter is None:
            return

        department = recruiter.department
        from apps.organization.models import Designation

        designation = Designation.objects.filter(department=department).first() \
            or Designation.objects.first()

        from apps.accounts.models import Role

        target_role = Role.objects.filter(code="cre", is_active=True).first()             or Role.objects.filter(code="employee", is_active=True).first()

        job, _ = JobOpening.objects.update_or_create(
            title="Front Desk Executive",
            defaults={
                "workflow": workflow, "department": department,
                "designation": designation, "location": recruiter.location,
                "recruiter": recruiter, "openings_count": 2,
                "target_role": target_role,
                "status": JobStatus.PUBLISHED, "published_at": timezone.now(),
                "description": "Greets visitors, manages appointments and the front desk.",
                "requirements": "Two years in a customer-facing role.",
            },
        )

        # Every application starts at the workflow's first stage — that is what
        # "applied" means, and the column is NOT NULL for exactly that reason.
        first_stage = workflow.stages.order_by("order").first()
        if first_stage is None:
            return

        applicants = [
            ("Nikhil", "Bhat", "nikhil.bhat@applicant.example"),
            ("Farida", "Sheikh", "farida.sheikh@applicant.example"),
            ("Rohit", "Kamble", "rohit.kamble@applicant.example"),
        ]
        made = 0
        for first, last, email in applicants:
            candidate, _ = Candidate.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first, "last_name": last, "phone": "",
                    # The model refuses PII with no lawful basis - a real
                    # applicant consents on the public form, so say so here.
                    "consent_given": True,
                },
            )
            _, created = Application.objects.get_or_create(
                candidate=candidate, job_opening=job,
                defaults={"applied_at": timezone.now(), "current_stage": first_stage},
            )
            made += int(created)
        self.stdout.write(f"  recruitment        1 published job, {len(applicants)} candidates")

    # -- a joiner behind the gate ----------------------------------------
    def _joiner(self, people):
        """
        One employee still inside onboarding, so the gate itself is testable:
        sign in as them and every module except onboarding is refused until
        HR approves their documents.
        """
        from apps.employees.services.creation import create_employee

        actor = self._actor(people)
        manager = people.get("hr_manager") or people.get("hr_head")
        template = people.get("cre") or next(iter(people.values()))
        email = f"new.joiner@{self.domain}"

        from apps.accounts.models import User

        if User.objects.filter(email=email).exists():
            self.stdout.write("  onboarding         joiner already present")
            return
        if manager is None or template.department_id is None:
            return

        from apps.organization.models import EmployeeLevel

        level = EmployeeLevel.objects.filter(layer=5).first()
        result = create_employee(
            actor=actor, first_name="Newly", last_name="Joined",
            email=email, personal_email=f"newly.joined.personal@{self.domain}",
            role_code="employee",
            department_id=template.department_id,
            designation_id=template.designation_id,
            location_id=template.location_id,
            level_id=level.pk if level else None,
            reporting_manager_id=manager.pk,
            date_of_joining=dt.date.today(),
            temporary_password="Onboard!Demo-2026",
        )
        self.stdout.write(
            f"  onboarding         {result.employee.employee_code} is behind the gate "
            f"(password: Onboard!Demo-2026)"
        )

    def _actor(self, people):
        from apps.accounts.models import User

        return (
            User.objects.filter(user_roles__role__code="admin", is_active=True)
            .order_by("-is_superuser").first()
        )

    # ---------------------------------------------------------------- close
    def _finish(self):
        from pathlib import Path

        creds = Path(settings.MEDIA_ROOT) / "demo-credentials.txt"
        url = settings.FRONTEND_URL.rstrip("/")
        self.stdout.write(self.style.SUCCESS(f"\n{self.company} is ready.\n"))
        self.stdout.write(f"  Sign in at   {url}/login")
        self.stdout.write(f"  Credentials  {creds}")
        self.stdout.write(
            "\n  Start with hr_head@ (people, leave, recruitment) and finance_head@ "
            "(payroll).\n  Every account must set its own password on first sign-in.\n"
        )
        self.stdout.write(self.style.WARNING(
            "  Statutory rate sets are DRAFTS on purpose — certify them as the "
            "Finance Head\n  (Payroll > Settings) before a payroll run can be "
            "approved. That is a\n  workflow worth testing, not a defect.\n"
        ))
