"""
Operator commands name the organization they act on.

These are the product's maintenance interface -- purge staging PII, roll leave
balances over, recompute a range of attendance, check the device integration --
and every one of them wrote or read organization-owned rows without ever saying
whose. That was invisible while the manager did not filter, and becomes an
`OrgContextMissing` the moment their app does.

It was invisible for a second reason too, which this file is here to end: no
test ran a management command. They were "the operator's interface, exercised by
hand", so the suite could be entirely green while `manage.py leave_rollover` was
broken for every deployment with more than one customer.

The rule each command inherits from `OrganizationCommand`: act on the named
organization, accept the only one when a deployment has exactly one, and REFUSE
to guess when there are several. Refusing matters more than it looks -- rolling
over the wrong company's balances, or recomputing a different company's employee
who happens to share an employee code, reports success either way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

#: (command name, extra kwargs). Each runs for real against a seeded
#: organization; the assertion is that it completes rather than raising
#: OrgContextMissing on its first query.
COMMANDS = [
    ("purge_import_staging", {}),
    ("leave_rollover", {"year": 2025}),
    (
        "recompute_attendance",
        {
            "date_from": str(dt.date(2025, 1, 6)),
            "date_to": str(dt.date(2025, 1, 7)),
        },
    ),
    ("essl_check", {}),
    ("audit_candidate_identity", {}),
    ("purge_candidates", {}),
    ("repair_application_stages", {}),
    ("seed_roles", {}),
]


@pytest.mark.parametrize(
    ("command", "kwargs"), COMMANDS, ids=[name for name, _ in COMMANDS]
)
def test_a_command_runs_for_the_organization_it_is_given(command, kwargs, org_a):
    call_command(command, organization=org_a.slug, **kwargs)


@pytest.mark.parametrize(
    ("command", "kwargs"), COMMANDS, ids=[name for name, _ in COMMANDS]
)
def test_a_command_refuses_to_guess_between_organizations(
    command, kwargs, org_a, org_b
):
    """
    With two customers and no slug, stopping is the only safe answer.

    Picking the first would act on somebody chosen by primary-key order, and
    every one of these commands reports success either way.
    """
    with pytest.raises(CommandError) as refusal:
        call_command(command, **kwargs)

    assert "organization" in str(refusal.value).lower()


def test_a_command_accepts_the_only_organization_without_being_told(organization):
    """
    The single-company case, which must not need a flag.

    A self-hosted deployment has exactly one organization and should never have
    to name it. This is the same reason the base class does not simply demand
    the option.

    Deliberately takes the session organization rather than the `org_a`
    fixture: that fixture BUILDS a second company, so asking for the single-org
    behaviour while it exists is a contradiction -- which is how the first
    version of this test failed.
    """
    from apps.organization.models import Organization

    assert Organization.objects.count() == 1, (
        "this test is only meaningful while exactly one organization exists"
    )

    call_command("purge_import_staging")


def _structure_of(organization):
    """Every department, location and level one organization owns, by content."""
    from apps.organization.models import Department, EmployeeLevel, Location

    return {
        model.__name__: sorted(
            model.objects.all_orgs()
            .filter(organization=organization)
            .values_list("pk", "name", "code")
        )
        for model in (Department, Location, EmployeeLevel)
    }


def test_seed_demo_seeds_its_own_organization_and_leaves_another_untouched(
    org_a, org_b, settings
):
    """
    The demo seeder upserted structure by code, and codes are unique per company.

    That is not hypothetical here: the other organization already owns
    department `HR`, location `HO` and levels `L2` and `L5` -- the exact codes
    this command writes. A bare `update_or_create(code="HR")` matches that row,
    so seeding one company's demo rewrote another company's People department.
    """
    from apps.organization.models import Department, OrganizationMembership

    settings.DEBUG = True
    call_command("seed_workflows", organization=org_a.slug)

    other_before = _structure_of(org_b.organization)
    assert any(code == "HR" for _, _, code in other_before["Department"]), (
        "the other organization must own an `HR` department, or this proves nothing"
    )

    call_command("seed_demo", organization=org_a.slug)

    assert _structure_of(org_b.organization) == other_before, (
        "seeding one organization's demo changed another organization's structure"
    )

    # Positive control: the demo really was built, in the right place.
    seeded_codes = set(
        Department.objects.all_orgs()
        .filter(organization=org_a.organization)
        .values_list("code", flat=True)
    )
    assert {"MED", "OPS", "HR", "FIN"} <= seeded_codes

    # And every demo login belongs to that organization. Without a membership
    # a user resolves to no organization and is denied everything, which is
    # what this command used to produce.
    from apps.accounts.models import User

    demo_users = User.objects.filter(email__endswith="@demo.test")
    assert demo_users.exists()
    for user in demo_users:
        assert OrganizationMembership.objects.filter(
            user=user, organization=org_a.organization
        ).exists(), f"{user.email} has no membership in {org_a.slug}"


DEMO_DOMAIN = "demo-company.example"


def test_seed_demo_company_builds_its_own_company_and_touches_no_other(
    org_a, org_b, settings, tmp_path
):
    """
    This command used to rename an arbitrary organization and hire into another.

    It picked "an organization with an admin, else the first", renamed it, and
    acted as "the first admin on the platform" -- and `create_employee` places a
    new hire in the ACTOR's organization. With two customers, one could be
    renamed and the other could receive the demo staff.
    """
    from apps.accounts.models import User
    from apps.employees.models import Employee
    from apps.organization.models import Organization, OrganizationMembership

    settings.MEDIA_ROOT = tmp_path
    other = org_b.organization
    other_identity = (other.name, other.slug)
    other_structure = _structure_of(other)

    call_command(
        "seed_demo_company",
        organization=org_a.slug,
        company="Tenancy Demo Co",
        domain=DEMO_DOMAIN,
    )

    other.refresh_from_db()
    assert (other.name, other.slug) == other_identity, "another organization was renamed"
    assert _structure_of(other) == other_structure, "another organization's structure changed"

    # Derived from the profile rather than written down: the roster is data
    # now, and a hard-coded count would make adding a person to a demo company
    # look like a tenancy failure.
    from apps.organization.demo import HEALTHCARE

    expected = len(HEALTHCARE.people) + len(HEALTHCARE.system_people)
    demo_users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
    assert demo_users.count() == expected, "the profile's whole roster, and only it"
    for user in demo_users:
        assert OrganizationMembership.objects.filter(
            user=user, organization=org_a.organization
        ).exists(), f"{user.email} is not a member of {org_a.slug}"

    demo_employees = Employee.objects.all_orgs().filter(
        user__email__endswith=f"@{DEMO_DOMAIN}"
    )
    assert demo_employees.exists()
    assert set(demo_employees.values_list("organization_id", flat=True)) == {
        org_a.organization.pk
    }, "a demo employee was hired into another organization"

    # The credentials file sits inside the seeded organization's own subtree,
    # so a second organization's demo cannot overwrite it.
    renamed = Organization.objects.get(pk=org_a.organization.pk)
    expected = tmp_path / "organizations" / str(renamed.pk) / "demo-credentials.txt"
    assert expected.exists()


def test_seed_demo_company_remove_takes_only_its_own_members(
    org_a, org_b, settings, tmp_path
):
    """
    `--remove` selected by email DOMAIN, across every organization.

    An account elsewhere that happened to share the domain went with the demo.
    Seeded here as an outsider in the other organization on the same domain.
    """
    from apps.accounts.models import User
    from apps.organization.models import OrganizationMembership

    settings.MEDIA_ROOT = tmp_path
    call_command(
        "seed_demo_company", organization=org_a.slug, domain=DEMO_DOMAIN
    )
    # Seeding renames the organization to the demo company, slug included --
    # that is the command's contract -- so the slug it answers to changed.
    org_a.organization.refresh_from_db()
    seeded_slug = org_a.organization.slug

    outsider = User.objects.create_user(
        email=f"outsider@{DEMO_DOMAIN}", password="not-a-demo-password-123"
    )
    OrganizationMembership.objects.create(
        organization=org_b.organization, user=outsider
    )

    call_command(
        "seed_demo_company", organization=seeded_slug, domain=DEMO_DOMAIN, remove=True
    )

    assert User.objects.filter(pk=outsider.pk).exists(), (
        "removing one organization's demo deleted an account in another"
    )
    remaining = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").exclude(
        pk=outsider.pk
    )
    assert not remaining.exists(), "the seeded organization's demo accounts were not removed"


def test_an_unknown_slug_is_refused_by_name(org_a):
    with pytest.raises(CommandError) as refusal:
        call_command("purge_import_staging", organization="no-such-company")

    assert "no-such-company" in str(refusal.value)


# ------------------------------------------------------------ recruitment
#
# These three are the destructive half of the operator interface: one
# anonymises personal data, one moves candidates through a hiring pipeline, and
# one retires candidate rows. Running without error proves little about them,
# so each is run with --apply against organization A while organization B holds
# a row that qualifies for exactly the same treatment -- and B's row must come
# out untouched. Each also asserts that A's row WAS changed, because a command
# that had stopped doing anything would pass the isolation half on its own.


def _acting_for(world):
    from core.middleware import acting_as

    return acting_as(None, organization=world.organization)


def test_purge_candidates_anonymises_only_the_named_organizations_candidates(
    org_a, org_b, settings
):
    from django.utils import timezone

    from apps.recruitment.models import Candidate, LegalBasis

    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    due = {}
    for world in (org_a, org_b):
        with _acting_for(world):
            candidate = Candidate.objects.create(
                first_name="Overdue",
                last_name="Import",
                email=f"overdue@{world.slug}.example",
                source="workindia",
                consent_given=False,
                legal_basis=LegalBasis.VOLUNTARILY_PROVIDED,
                notice_due_at=timezone.now(),
            )
        Candidate.objects.all_orgs().filter(pk=candidate.pk).update(
            created_at=timezone.now() - dt.timedelta(days=120)
        )
        due[world.slug] = candidate.pk

    call_command("purge_candidates", organization=org_a.slug, apply=True)

    names = dict(
        Candidate.objects.all_orgs()
        .filter(pk__in=due.values())
        .values_list("pk", "first_name")
    )
    assert names[due[org_a.slug]] == "Redacted", "the named organization's overdue candidate was not purged"
    assert names[due[org_b.slug]] == "Overdue", (
        "purging one organization anonymised another organization's candidate"
    )


def test_repair_application_stages_moves_only_the_named_organizations_applications(
    org_a, org_b
):
    from apps.recruitment.models import Application, ApplicationEvent, ApplicationStatus
    from apps.workflows.models import StageKind, WorkflowStage

    for world in (org_a, org_b):
        with _acting_for(world):
            WorkflowStage.objects.create(
                workflow=world.rows["hiring_workflow"],
                name="Not selected",
                order=90,
                kind=StageKind.TERMINAL,
                is_terminal=True,
                is_won=False,
            )
        # Closed status at a live stage: the incoherent shape the command repairs.
        Application.objects.all_orgs().filter(pk=world.rows["application"].pk).update(
            status=ApplicationStatus.REJECTED
        )

    call_command("repair_application_stages", organization=org_a.slug, apply=True)

    def stage_of(world):
        return (
            Application.objects.all_orgs()
            .select_related("current_stage")
            .get(pk=world.rows["application"].pk)
            .current_stage
        )

    assert stage_of(org_a).is_terminal, "the named organization's application was not repaired"
    assert stage_of(org_b).pk == org_b.rows["workflow_stage"].pk, (
        "repairing one organization moved another organization's candidate"
    )
    assert not ApplicationEvent.objects.all_orgs().filter(
        application_id=org_b.rows["application"].pk,
        actor_label="system · stage repair",
    ).exists(), "a repair event was written into another organization's history"


#: Everything `google_forms_check` prints, reporting a working integration, so
#: the `--create-for` half can be reached without Google credentials.
READY_REPORT = {
    "credentials": True,
    "kind": "service_account",
    "client_email": "forms@example.test",
    "token": True,
    "drive_api": True,
    "forms_api": True,
    "file_upload_question": False,
    "folder_id": "folder",
    "share_with": "",
    "errors": {},
}


@pytest.fixture
def forms_ready(monkeypatch):
    """A ready integration, recording which jobs a form was created for."""
    from apps.recruitment.services import external_forms

    created = []
    monkeypatch.setattr(external_forms, "diagnose", lambda: READY_REPORT)
    monkeypatch.setattr(
        external_forms, "create_external_form", lambda job: created.append(job.pk)
    )
    return created


def test_google_forms_check_will_not_build_a_form_for_another_organizations_job(
    org_a, org_b, forms_ready
):
    """
    `--create-for` took a bare job id and fetched it from every organization.

    Refused as "not found" -- the same answer as a job that does not exist, so
    the command does not confirm that the id belongs to someone.
    """
    other_job = org_b.rows["job_opening"]

    with pytest.raises(CommandError) as refusal:
        call_command(
            "google_forms_check", organization=org_a.slug, create_for=str(other_job.pk)
        )

    assert "No job opening" in str(refusal.value)
    assert org_b.slug not in str(refusal.value)
    assert forms_ready == [], "a form was built for another organization's job"

    # Positive control: the same command, with the organization's own job.
    own_job = org_a.rows["job_opening"]
    call_command(
        "google_forms_check", organization=org_a.slug, create_for=str(own_job.pk)
    )
    assert forms_ready == [own_job.pk]


def test_google_forms_check_names_an_organization_only_for_the_job_half(
    org_a, org_b, forms_ready
):
    """
    The credential check is about the deployment and needs no organization.
    Building a job's form is about one organization, and refuses to guess.
    """
    call_command("google_forms_check")  # must not demand a slug

    with pytest.raises(CommandError) as refusal:
        call_command("google_forms_check", create_for=str(org_a.rows["job_opening"].pk))

    assert "organization" in str(refusal.value).lower()
    assert forms_ready == []


def test_seed_all_keeps_its_organization_bound_through_every_step(
    organization, settings, tmp_path
):
    """
    `seed_all` bound the organization for its configuration seeds and nothing
    after them.

    People and transactional data were then written with no organization
    bound, which raised `OrgContextMissing` on the first attendance row once
    that app filtered at the manager. The command is the documented way to get
    a hand-testable system, and nothing ran it.

    Single-organization on purpose: the command refuses to choose between
    several, so this is the only shape in which it does any work.
    """
    from apps.attendance.models import AttendanceRecord
    from apps.recruitment.models import Application, JobOpening

    settings.MEDIA_ROOT = tmp_path

    call_command("seed_all", force=True, domain=DEMO_DOMAIN)

    job = JobOpening.objects.all_orgs().get(title="Front Desk Executive")
    assert job.organization_id == organization.pk
    assert Application.objects.all_orgs().filter(
        job_opening=job, organization=organization
    ).exists(), "the recruitment step wrote no applications"
    assert AttendanceRecord.objects.all_orgs().filter(
        organization=organization, employee__user__email__endswith=f"@{DEMO_DOMAIN}"
    ).exists(), "the attendance step wrote no records"


def test_seed_roles_exports_only_the_named_organizations_matrix(org_a, org_b, tmp_path):
    """
    `--csv` read every permission row in the database.

    So one customer's export was every customer's matrix concatenated,
    including the cells each had customized. It must contain exactly the named
    organization's active permission rows.
    """
    import csv

    from apps.accounts.models import RolePermission

    out = tmp_path / "matrix.csv"
    call_command("seed_roles", organization=org_a.slug, csv=str(out))

    with out.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    expected = RolePermission.objects.all_orgs().filter(
        organization=org_a.organization, is_active=True
    ).count()
    other = RolePermission.objects.all_orgs().filter(
        organization=org_b.organization, is_active=True
    ).count()
    assert other, "the other organization must have a matrix, or this proves nothing"
    assert len(rows) == expected, (
        f"exported {len(rows)} rows; the organization has {expected} and the "
        f"other has {other}"
    )
