"""
A demo organisation, for exercising the SPA against a real backend.

DEVELOPMENT ONLY. Refuses to run when DEBUG is off, because it creates accounts
with known passwords — exactly what you never want reachable in production.

Idempotent: safe to re-run. It creates the org structure, one person per role,
and a small recruitment pipeline in various states so every screen has
something real to render.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

DEMO_PASSWORD = "demo-password-12345"


class Command(BaseCommand):
    help = "Seed a demo organisation, staff and recruitment pipeline (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when DEBUG is off. Never use this on a real deployment.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_demo creates accounts with a known password and refuses to run "
                "with DEBUG off. Pass --force only if you are certain this is a "
                "throwaway environment."
            )

        from apps.accounts.models import Role, User, UserRole
        from apps.employees.models import Employee
        from apps.organization.models import (
            Department,
            Designation,
            EmployeeLevel,
            Location,
            OrgSettings,
        )
        from apps.recruitment.models import Application, Candidate, JobOpening, JobStatus
        from apps.workflows.models import HiringWorkflow
        from core.access.catalog import DepartmentKind, Layer

        roles = {role.code: role for role in Role.objects.all()}
        if not roles:
            raise CommandError("Run `manage.py seed_roles` first.")
        if not HiringWorkflow.objects.exists():
            raise CommandError("Run `manage.py seed_workflows` first.")

        # --- organisation ------------------------------------------------
        from apps.organization.models import Organization, OrgStatus

        organization = Organization.objects.first()
        if organization is None:
            organization = Organization.objects.create(
                name="Demo Health Dev", slug="demo-health-dev", status=OrgStatus.ACTIVE
            )
        OrgSettings.for_org(organization)

        # `state` is not decoration: Professional Tax is a state levy, and a
        # location without one has no PT jurisdiction, so payroll would silently
        # deduct nothing for everybody working there.
        location, _ = Location.objects.update_or_create(
            code="HO",
            defaults={
                "name": "Head Office", "city": "Pune", "state": "MH",
                "is_head_office": True,
            },
        )

        departments = {}
        for kind, name, code in [
            (DepartmentKind.MEDICAL, "Medical", "MED"),
            (DepartmentKind.OPERATIONS, "Operations", "OPS"),
            (DepartmentKind.HR, "Human Resources", "HR"),
            (DepartmentKind.FINANCE, "Finance", "FIN"),
        ]:
            departments[kind], _ = Department.objects.update_or_create(
                code=code, defaults={"name": name, "kind": kind}
            )

        levels = {}
        for layer, name, code, rank in [
            (Layer.LEADERSHIP, "Leadership", "L1", 10),
            (Layer.DEPARTMENT_HEAD, "Department Head", "L2", 20),
            (Layer.MANAGER, "Manager", "L3", 30),
            (Layer.EXECUTIVE, "Executive", "L4", 40),
            (Layer.STAFF, "Staff", "L5", 50),
        ]:
            levels[layer], _ = EmployeeLevel.objects.update_or_create(
                code=code, defaults={"name": name, "layer": layer, "rank": rank}
            )

        designations = {}
        for title, kind in [
            ("Therapist", DepartmentKind.MEDICAL),
            ("Clinic Doctor", DepartmentKind.MEDICAL),
            ("Office Boy", DepartmentKind.OPERATIONS),
            ("HR Executive", DepartmentKind.HR),
        ]:
            designations[title], _ = Designation.objects.update_or_create(
                title=title, department=departments[kind], defaults={}
            )

        # --- people ------------------------------------------------------
        staff: dict[str, Employee] = {}
        counter = [100]

        def hire(role_code: str, kind: str, first: str, last: str, layer) -> Employee:
            counter[0] += 1
            email = f"{role_code}@demo.test"
            user, created = User.objects.get_or_create(
                email=email, defaults={"first_name": first, "last_name": last}
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            UserRole.objects.get_or_create(user=user, role=roles[role_code])
            employee, _ = Employee.objects.update_or_create(
                user=user,
                defaults={
                    "employee_code": f"EMP{counter[0]:05d}",
                    "first_name": first,
                    "last_name": last,
                    "work_email": email,
                    "department": departments[kind],
                    "level": levels[layer],
                    "location": location,
                    "date_of_joining": dt.date(2022, 4, 1),
                },
            )
            staff[role_code] = employee
            return employee

        hire("hr_head", DepartmentKind.HR, "Hema", "Rao", Layer.DEPARTMENT_HEAD)
        hire("hr_manager", DepartmentKind.HR, "Hari", "Menon", Layer.MANAGER)
        hire("recruiter", DepartmentKind.HR, "Ravi", "Shah", Layer.EXECUTIVE)
        hire("medical_director", DepartmentKind.MEDICAL, "Meera", "Kulkarni", Layer.DEPARTMENT_HEAD)
        hire("senior_doctor", DepartmentKind.MEDICAL, "Sanjay", "Iyer", Layer.MANAGER)
        hire("clinic_doctor", DepartmentKind.MEDICAL, "Chandni", "Bose", Layer.EXECUTIVE)
        hire("therapist", DepartmentKind.MEDICAL, "Tara", "Nair", Layer.STAFF)
        hire("operational_head", DepartmentKind.OPERATIONS, "Oindrila", "Sen", Layer.DEPARTMENT_HEAD)
        hire("operations_manager", DepartmentKind.OPERATIONS, "Omkar", "Patil", Layer.MANAGER)
        hire("cre", DepartmentKind.OPERATIONS, "Chetan", "Desai", Layer.EXECUTIVE)
        hire("finance_head", DepartmentKind.FINANCE, "Farah", "Khan", Layer.DEPARTMENT_HEAD)
        hire("office_boy", DepartmentKind.OPERATIONS, "Om", "Jadhav", Layer.STAFF)

        # Every remaining grantable role gets a holder too. A role nobody can
        # sign in as is indistinguishable, in a demo, from a role that does not
        # work. The finance pair is also what makes segregation of duties
        # visible: accounts_manager and payroll_executive PROCESS payroll,
        # finance_head APPROVES it, and no single account does both.
        hire("accounts_manager", DepartmentKind.FINANCE, "Anita", "Kelkar", Layer.MANAGER)
        hire("payroll_executive", DepartmentKind.FINANCE, "Pooja", "Reddy", Layer.EXECUTIVE)
        hire("executive", DepartmentKind.OPERATIONS, "Esha", "Kapoor", Layer.EXECUTIVE)
        hire("employee", DepartmentKind.OPERATIONS, "Ekta", "Sharma", Layer.STAFF)

        # CEO and Admin are system principals with no Employee record — that is
        # the approved design, not an omission.
        for code, first in [("ceo", "Chair"), ("admin", "Root")]:
            user, created = User.objects.get_or_create(
                email=f"{code}@demo.test", defaults={"first_name": first}
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
                if code == "admin":
                    user.is_staff = True
                    user.is_superuser = True
                    user.save(update_fields=["is_staff", "is_superuser"])
            UserRole.objects.get_or_create(user=user, role=roles[code])

        # --- jobs --------------------------------------------------------
        therapist_workflow = HiringWorkflow.objects.get(name="Therapist hiring")
        office_workflow = HiringWorkflow.objects.get(name="Office Boy hiring")

        therapist_job, _ = JobOpening.objects.update_or_create(
            title="Therapist",
            defaults={
                "workflow": therapist_workflow,
                "department": departments[DepartmentKind.MEDICAL],
                "designation": designations["Therapist"],
                "location": location,
                "level": levels[Layer.STAFF],
                "target_role": roles["therapist"],
                "status": JobStatus.PUBLISHED,
                "published_at": timezone.now(),
                "recruiter": staff["recruiter"],
                "openings_count": 2,
                "description": "Deliver therapy sessions in the clinic.",
                "requirements": "Relevant qualification and registration.",
            },
        )
        office_job, _ = JobOpening.objects.update_or_create(
            title="Office Boy",
            defaults={
                "workflow": office_workflow,
                "department": departments[DepartmentKind.OPERATIONS],
                "designation": designations["Office Boy"],
                "location": location,
                "level": levels[Layer.STAFF],
                "target_role": roles["office_boy"],
                "status": JobStatus.PUBLISHED,
                "published_at": timezone.now(),
                "recruiter": staff["recruiter"],
                "description": "Support day-to-day clinic operations.",
            },
        )

        # --- candidates, at a spread of stages ---------------------------
        from apps.recruitment.services.engine import record_decision
        from apps.recruitment.services.interviews import schedule_interview, submit_feedback
        from apps.workflows.models import Decision

        def candidate_at(job, first, last, email, advance_to: int):
            candidate, _ = Candidate.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": "9000000000",
                    "consent_given": True,
                    "consent_at": timezone.now(),
                    "current_employer": "Previous Clinic",
                    "total_experience_years": "3.5",
                    "expected_ctc": "600000.00",
                    "notice_period_days": 30,
                },
            )
            application, created = Application.objects.get_or_create(
                candidate=candidate,
                job_opening=job,
                defaults={"current_stage": job.workflow.first_stage},
            )
            if not created:
                return application

            interview_roles = (
                ("clinic_doctor", "senior_doctor")
                if job.workflow_id == therapist_workflow.pk
                else ("cre", "operations_manager")
            )
            slot = timezone.now() + dt.timedelta(days=3, hours=counter[0] % 12)

            if advance_to >= 20:
                record_decision(
                    application=application,
                    actor=staff["recruiter"].user,
                    decision=Decision.PASS,
                )
            if advance_to >= 30:
                record_decision(
                    application=application,
                    actor=staff["hr_manager"].user,
                    decision=Decision.VERIFY,
                )
            for index, (order, role_code) in enumerate(zip((30, 40), interview_roles)):
                if advance_to <= order:
                    break
                stage = job.workflow.stages.get(order=order)
                interview = schedule_interview(
                    application=application,
                    stage=stage,
                    interviewer=staff[role_code],
                    actor=staff["recruiter"].user,
                    scheduled_at=slot + dt.timedelta(hours=index * 2),
                )
                answers = {
                    field.key: (
                        4 if field.kind == "rating_1_5" else True if field.kind == "boolean" else "ok"
                    )
                    for field in stage.feedback_form.fields.filter(is_required=True)
                }
                submit_feedback(
                    interview=interview,
                    actor=staff[role_code].user,
                    answers=answers,
                    recommendation="hire",
                    strengths="Strong communication and relevant experience.",
                    concerns="None material.",
                    overall_rating=4,
                )
                record_decision(
                    application=application,
                    actor=staff[role_code].user,
                    decision=Decision.PASS,
                )
            if advance_to >= 60:
                head = (
                    "medical_director"
                    if job.workflow_id == therapist_workflow.pk
                    else "operational_head"
                )
                record_decision(
                    application=application,
                    actor=staff[head].user,
                    decision=Decision.RECOMMEND_SELECT,
                    rationale="Strong across every round; recommend proceeding to offer.",
                )
            return application

        candidate_at(therapist_job, "Asha", "Deshmukh", "asha@demo.test", 20)
        candidate_at(therapist_job, "Bhavna", "Rao", "bhavna@demo.test", 30)
        candidate_at(therapist_job, "Chirag", "Mehta", "chirag@demo.test", 50)
        candidate_at(therapist_job, "Divya", "Kamat", "divya@demo.test", 60)
        candidate_at(office_job, "Eshan", "Pawar", "eshan@demo.test", 30)
        candidate_at(office_job, "Farida", "Sheikh", "farida@demo.test", 60)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo organisation seeded.\n"
                f"  Sign in with <role>@demo.test / {DEMO_PASSWORD}\n"
                "  e.g. hr_head@demo.test, medical_director@demo.test, "
                "clinic_doctor@demo.test, recruiter@demo.test, ceo@demo.test, admin@demo.test"
            )
        )
