"""
Authoring hiring workflows from a process description.

The central claim: an admin describes WHO does WHAT in WHICH order, and the
generated graph is structurally indistinguishable from a seeded one — same
stage kinds, same advisory-rejection routing, same single point of terminal
authority. If these hold, the engine cannot tell an authored workflow from a
seeded one, which is exactly the point.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.workflows import services
from apps.workflows.models import Decision, HiringWorkflow, StageKind

pytestmark = pytest.mark.django_db

BASE = "/api/v1/workflows/"


@pytest.fixture
def admin(make_user):
    return make_user("admin")


@pytest.fixture
def draft(admin, roles):
    return services.create_workflow(
        actor=admin,
        name="Radiologist hiring",
        description="Two clinical rounds, then the medical director.",
        department_kind="medical",
        interview_rounds=[
            {"name": "Round 1 — Clinic Doctor", "role": "clinic_doctor"},
            {"name": "Round 2 — Senior Doctor", "role": "senior_doctor"},
        ],
        recommendation_role="medical_director",
    )


def _stages(workflow):
    return list(workflow.stages.filter(is_active=True).order_by("order"))


# ------------------------------------------------------------- generation


def test_the_generated_graph_has_the_canonical_shape(draft):
    stages = _stages(draft)
    kinds = [stage.kind for stage in stages]

    assert kinds == [
        StageKind.APPLICATION,
        StageKind.HR_VERIFICATION,
        StageKind.INTERVIEW,
        StageKind.INTERVIEW,
        StageKind.DEPARTMENT_DECISION,
        StageKind.HR_FINAL_DECISION,
        StageKind.OFFER,
        StageKind.ONBOARDING,
        StageKind.TERMINAL,
        StageKind.TERMINAL,
    ]


def test_each_round_carries_its_interviewer(draft):
    rounds = [s for s in _stages(draft) if s.kind == StageKind.INTERVIEW]

    assert rounds[0].responsible_role.code == "clinic_doctor"
    assert rounds[1].responsible_role.code == "senior_doctor"
    assert all(stage.requires_interview for stage in rounds)


def test_exactly_one_stage_holds_terminal_authority(draft):
    finals = [s for s in _stages(draft) if s.is_final_hr_decision]

    assert len(finals) == 1
    assert finals[0].responsible_role.code == "hr_head"
    assert set(finals[0].allowed_decisions) == {"select", "reject"}


def test_an_interviewers_no_routes_to_the_decision_not_around_it(draft):
    """Advisory rejection ends at HR Head's desk, never at the candidate."""
    rounds = [s for s in _stages(draft) if s.kind == StageKind.INTERVIEW]
    final = next(s for s in _stages(draft) if s.is_final_hr_decision)

    for stage in rounds:
        transition = stage.outgoing_transitions.get(on_decision=Decision.RECOMMEND_REJECT)
        assert transition.to_stage_id == final.pk


def test_every_route_ends_in_a_terminal(draft):
    stages = _stages(draft)
    won = [s for s in stages if s.is_won]
    terminal = [s for s in stages if s.is_terminal]

    assert len(won) == 1
    assert len(terminal) == 2


def test_the_recommendation_stage_is_optional(admin, roles):
    workflow = services.create_workflow(
        actor=admin,
        name="Direct-to-HR hiring",
        interview_rounds=[{"role": "operations_manager"}],
    )
    kinds = [s.kind for s in _stages(workflow)]

    assert StageKind.DEPARTMENT_DECISION not in kinds
    # The single round's PASS lands on the final decision directly.
    the_round = next(s for s in _stages(workflow) if s.kind == StageKind.INTERVIEW)
    final = next(s for s in _stages(workflow) if s.is_final_hr_decision)
    assert the_round.outgoing_transitions.get(on_decision="pass").to_stage_id == final.pk


def test_at_least_one_round_is_required(admin, roles):
    with pytest.raises(ValidationError):
        services.create_workflow(actor=admin, name="No interviews", interview_rounds=[])


def test_an_unknown_role_is_refused(admin, roles):
    with pytest.raises(ValidationError):
        services.create_workflow(
            actor=admin, name="Ghost round", interview_rounds=[{"role": "astronaut"}]
        )


# -------------------------------------------------------------- lifecycle


def test_a_draft_is_not_offered_to_job_forms(draft):
    """The job serializer refuses drafts; the flag is what it reads."""
    assert draft.is_published is False


def test_publishing_freezes_structure(admin, draft):
    services.publish_workflow(actor=admin, workflow=draft)
    draft.refresh_from_db()
    assert draft.is_published is True

    with pytest.raises(ValidationError):
        services.update_workflow(
            actor=admin,
            workflow=draft,
            interview_rounds=[{"role": "clinic_doctor"}],
        )


def test_a_published_workflow_may_still_be_renamed(admin, draft):
    services.publish_workflow(actor=admin, workflow=draft)

    updated = services.update_workflow(
        actor=admin, workflow=draft, name="Radiologist hiring (2026)"
    )

    assert updated.name == "Radiologist hiring (2026)"


def test_a_draft_may_be_restructured(admin, draft):
    services.update_workflow(
        actor=admin,
        workflow=draft,
        interview_rounds=[
            {"role": "clinic_doctor"},
            {"role": "senior_doctor"},
            {"name": "Round 3 — Panel", "role": "medical_director"},
        ],
        recommendation_role=None,
    )

    rounds = [s for s in _stages(draft) if s.kind == StageKind.INTERVIEW]
    assert len(rounds) == 3
    assert StageKind.DEPARTMENT_DECISION not in [s.kind for s in _stages(draft)]


def test_duplicate_names_are_refused(admin, draft, roles):
    with pytest.raises(ValidationError):
        services.create_workflow(
            actor=admin,
            name="Radiologist hiring",
            interview_rounds=[{"role": "clinic_doctor"}],
        )


def test_a_workflow_in_use_cannot_be_retired(admin, draft, org, roles, make_user):
    from apps.organization.models import Designation
    from apps.recruitment.models import JobOpening
    from core.access.catalog import DepartmentKind

    services.publish_workflow(actor=admin, workflow=draft)
    JobOpening.objects.create(
        title="Radiologist",
        workflow=draft,
        department=org["departments"][DepartmentKind.MEDICAL],
        designation=Designation.objects.create(title="Radiologist"),
        target_role=admin.user_roles.first().role,
        openings_count=1,
    )

    with pytest.raises(ValidationError):
        services.deactivate_workflow(actor=admin, workflow=draft)


def test_an_unused_workflow_retires_softly(admin, draft):
    services.deactivate_workflow(actor=admin, workflow=draft)
    draft.refresh_from_db()
    assert draft.is_active is False


# ------------------------------------------------------------ over HTTP


def test_the_full_authoring_flow_over_http(api, admin, roles):
    api.force_authenticate(user=admin)

    created = api.post(
        BASE,
        {
            "name": "Lab hiring",
            "department_kind": "medical",
            "interview_rounds": [
                {"name": "Round 1", "role": "clinic_doctor"},
                {"name": "Round 2", "role": "senior_doctor"},
            ],
            "recommendation_role": "medical_director",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["is_published"] is False
    assert len(created.data["stages"]) == 10

    published = api.post(f"{BASE}{created.data['id']}/publish/")
    assert published.status_code == 200
    assert published.data["is_published"] is True


def test_an_hr_head_may_author_but_a_recruiter_may_not(api, make_user, roles, org):
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    def hire(user, code):
        # Both roles require an employee record to resolve to anything.
        Employee.objects.create(
            employee_code=code,
            user=user,
            first_name=code,
            department=org["departments"][DepartmentKind.HR],
            date_of_joining="2024-01-01",
        )
        return user

    payload = {
        "name": "Recruiter attempt",
        "interview_rounds": [{"role": "clinic_doctor"}],
    }

    api.force_authenticate(user=hire(make_user("recruiter"), "EMP07751"))
    refused = api.post(BASE, payload, format="json")
    assert refused.status_code == 403

    api.force_authenticate(user=hire(make_user("hr_head"), "EMP07752"))
    allowed = api.post(BASE, payload, format="json")
    assert allowed.status_code == 201, allowed.data


def test_the_ceo_cannot_author(api, make_user, roles):
    api.force_authenticate(user=make_user("ceo"))

    response = api.post(
        BASE,
        {"name": "CEO attempt", "interview_rounds": [{"role": "clinic_doctor"}]},
        format="json",
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# A recruiter in the interview chair — the first real custom workflow did this
# ---------------------------------------------------------------------------


def test_publishing_refuses_a_stage_whose_role_cannot_record_its_decision(admin, roles):
    """
    The guard for the class of defect above: if a stage names a role that the
    matrix never equipped for that kind of decision, say so at publish time.
    Simulated by removing the grant from a copy of the role's permissions.
    """
    from apps.accounts.models import RolePermission

    workflow = services.create_workflow(
        actor=admin, name="Guarded", interview_rounds=[{"name": "Round 1", "role": "recruiter"}],
    )
    RolePermission.objects.filter(role=roles["recruiter"], resource="interview_feedback", action="create").delete()
    with pytest.raises(services.WorkflowError) as exc:
        services.publish_workflow(actor=admin, workflow=workflow)
    assert "Recruiter cannot record 'pass'" in str(exc.value.message_dict["stages"][0])
    assert "interview_feedback/create" in str(exc.value.message_dict["stages"][0])
    workflow.refresh_from_db()
    assert workflow.is_published is False


def test_the_http_default_verifier_is_the_recruiter(api, admin, roles):
    """
    The API had its own verification_role default (hr_manager) that survived
    the move of verification to the Recruiter — so workflows built over HTTP
    quietly disagreed with ones built by the service. Pin both doors.
    """
    api.force_authenticate(user=admin)
    response = api.post(
        "/api/v1/workflows/",
        {"name": "Default verifier", "interview_rounds": [{"role": "recruiter"}]},
        format="json",
    )
    assert response.status_code == 201, response.data
    stages = {s["kind"]: s for s in response.data["stages"]}
    assert stages["hr_verification"]["responsible_role_code"] == "recruiter"
    assert "screen_out" in stages["hr_verification"]["allowed_decisions"]


def test_a_retired_workflows_name_is_refused_cleanly(admin, draft):
    """
    The name is unique at the database across retired workflows too. Checking
    only the living let the clash reach the constraint as a bare 500.
    """
    services.deactivate_workflow(actor=admin, workflow=draft)
    with pytest.raises(services.WorkflowError, match="retired workflow still holds"):
        services.create_workflow(
            actor=admin, name=draft.name, interview_rounds=[{"role": "clinic_doctor"}],
        )


def test_department_heads_can_sit_in_the_interview_chair(admin, roles):
    """
    The office's real process: HR Manager takes the HR round, then the
    DEPARTMENT HEAD (Medical Director clinically, Operational Head for
    operations) conducts the second round themselves — no separate
    recommendation stage. That requires the head roles to hold
    INTERVIEW_FEEDBACK/CREATE, or the publish guard rightly refuses the
    very person the workflow put in the chair.
    """
    for name, head in (
        ("Clinical Position Workflow", "medical_director"),
        ("Operations Position Workflow", "operational_head"),
    ):
        workflow = services.create_workflow(
            actor=admin,
            name=name,
            interview_rounds=[
                {"name": "HR Interview", "role": "hr_manager"},
                {"name": "Head Interview", "role": head},
            ],
            recommendation_role=None,
        )
        services.publish_workflow(actor=admin, workflow=workflow)  # must not refuse
        kinds = [s.kind for s in _stages(workflow)]
        assert StageKind.DEPARTMENT_DECISION not in kinds
        rounds = [s for s in _stages(workflow) if s.kind == StageKind.INTERVIEW]
        assert [r.responsible_role.code for r in rounds] == ["hr_manager", head]


def test_the_august_2026_office_pipelines_publish(admin, roles):
    """
    The four pipelines the office actually runs, exactly as specified: no
    recommendation stage anywhere, the Recruiter verifying, and the named
    role in each chair — including Dr. Roy's senior-doctor round and the
    department heads conducting their own interviews.
    """
    SPECS = {
        "Clinical Workflow": ["hr_manager", "senior_doctor", "medical_director"],
        "CRE Workflow": ["hr_manager", "senior_doctor", "operational_head"],
        "Business Developer Workflow": ["hr_manager", "operational_head"],
        "Sales Workflow": ["hr_manager", "operational_head", "medical_director"],
    }
    for name, chairs in SPECS.items():
        workflow = services.create_workflow(
            actor=admin, name=name,
            interview_rounds=[{"role": chair} for chair in chairs],
            recommendation_role=None,
        )
        services.publish_workflow(actor=admin, workflow=workflow)  # must not refuse
        stages = _stages(workflow)
        assert StageKind.DEPARTMENT_DECISION not in [s.kind for s in stages]
        verification = next(s for s in stages if s.kind == StageKind.HR_VERIFICATION)
        assert verification.responsible_role.code == "recruiter"
        rounds = [s for s in stages if s.kind == StageKind.INTERVIEW]
        assert [r.responsible_role.code for r in rounds] == chairs
        final = next(s for s in stages if s.is_final_hr_decision)
        assert final.responsible_role.code == "hr_head"
