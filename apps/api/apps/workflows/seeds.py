"""
The two approved workflow configurations, as DATA.

Both are built by `build_workflow()` — one generic function driven by a
declarative spec. The Therapist and Office Boy pipelines differ only in the
contents of their spec dictionaries: same builder, same engine, no branch on
job title anywhere.

Adding a Nurse or Sales Executive pipeline means adding a spec here (or
creating one through the API), not writing code.
"""

from __future__ import annotations

from django.db import transaction

from core.access.catalog import DepartmentKind, RoleCode
from core.models import org_scoped

from .models import (
    Decision,
    FeedbackField,
    FeedbackFieldKind,
    FeedbackForm,
    HiringWorkflow,
    StageKind,
    StageTransition,
    WorkflowStage,
)

# ---------------------------------------------------------------------------
# Feedback forms
# ---------------------------------------------------------------------------

CLINICAL_ASSESSMENT = {
    "name": "Clinical assessment",
    "description": "Structured assessment for clinical interviews.",
    "fields": [
        ("clinical_knowledge", "Clinical / technical knowledge", FeedbackFieldKind.RATING_1_5, True),
        ("relevant_experience", "Relevant experience", FeedbackFieldKind.RATING_1_5, True),
        ("communication", "Communication", FeedbackFieldKind.RATING_1_5, True),
        ("professional_suitability", "Professional suitability", FeedbackFieldKind.RATING_1_5, True),
        ("patient_handling", "Patient handling and empathy", FeedbackFieldKind.RATING_1_5, False),
        ("notes", "Additional observations", FeedbackFieldKind.TEXT, False),
    ],
}

OPERATIONS_ASSESSMENT = {
    "name": "Operations assessment",
    "description": "Structured assessment for operational interviews.",
    "fields": [
        ("communication", "Communication", FeedbackFieldKind.RATING_1_5, True),
        ("reliability", "Reliability and punctuality", FeedbackFieldKind.RATING_1_5, True),
        ("workplace_suitability", "General workplace suitability", FeedbackFieldKind.RATING_1_5, True),
        ("job_understanding", "Understanding of the role", FeedbackFieldKind.RATING_1_5, True),
        ("availability", "Availability confirmed", FeedbackFieldKind.BOOLEAN, True),
        ("relevant_experience", "Relevant experience", FeedbackFieldKind.RATING_1_5, False),
        ("notes", "Additional observations", FeedbackFieldKind.TEXT, False),
    ],
}


# ---------------------------------------------------------------------------
# Workflow specs
# ---------------------------------------------------------------------------
# Each stage: (name, kind, role, decisions, requires_interview, feedback_form)
# Each transition: (from_order, decision, to_order)

THERAPIST_WORKFLOW = {
    "name": "Therapist hiring",
    "description": "Medical function: Clinic Doctor → Senior Doctor → Medical Director → HR Head.",
    "department_kind": DepartmentKind.MEDICAL,
    "feedback_form": CLINICAL_ASSESSMENT["name"],
    "stages": [
        # A submitted application lands DIRECTLY in Recruiter verification —
        # there is no "Application received" holding stage and no Pass step
        # before the recruiter's Verify.
        (
            20, "Recruiter verification", StageKind.HR_VERIFICATION, RoleCode.RECRUITER,
            [Decision.VERIFY, Decision.REQUEST_INFO, Decision.SCREEN_OUT], False, None,
        ),
        (
            30, "Clinic Doctor interview", StageKind.INTERVIEW, RoleCode.CLINIC_DOCTOR,
            [Decision.PASS, Decision.RECOMMEND_REJECT], True, CLINICAL_ASSESSMENT["name"],
        ),
        (
            40, "Senior Doctor interview", StageKind.INTERVIEW, RoleCode.SENIOR_DOCTOR,
            [Decision.PASS, Decision.RECOMMEND_REJECT], True, CLINICAL_ASSESSMENT["name"],
        ),
        (
            50, "Medical Director recommendation", StageKind.DEPARTMENT_DECISION,
            RoleCode.MEDICAL_DIRECTOR,
            [Decision.RECOMMEND_SELECT, Decision.RECOMMEND_REJECT], False, None,
        ),
        (
            60, "HR Head final decision", StageKind.HR_FINAL_DECISION, RoleCode.HR_HEAD,
            [Decision.SELECT, Decision.REJECT], False, None,
        ),
        (70, "Offer", StageKind.OFFER, RoleCode.HR_HEAD, [], False, None),
        (80, "Onboarding", StageKind.ONBOARDING, RoleCode.HR_MANAGER, [], False, None),
        (90, "Hired", StageKind.TERMINAL, None, [], False, None),
        (95, "Rejected", StageKind.TERMINAL, None, [], False, None),
    ],
    "transitions": [
        (20, Decision.VERIFY, 30),
        (20, Decision.REQUEST_INFO, 20),
        (20, Decision.SCREEN_OUT, 95),
        (30, Decision.PASS, 40),
        (30, Decision.RECOMMEND_REJECT, 60),
        (40, Decision.PASS, 50),
        (40, Decision.RECOMMEND_REJECT, 60),
        (50, Decision.RECOMMEND_SELECT, 60),
        (50, Decision.RECOMMEND_REJECT, 60),
        (60, Decision.SELECT, 70),
        (60, Decision.REJECT, 95),
        (70, Decision.OFFER_ACCEPTED, 80),
        (70, Decision.OFFER_DECLINED, 95),
        (80, Decision.PASS, 90),
    ],
    "final_stage_order": 60,
    "terminal_won_order": 90,
    "terminal_lost_order": 95,
}

OFFICE_BOY_WORKFLOW = {
    "name": "Office Boy hiring",
    "description": "Operations function: CRE → Operations Manager → Operational Head → HR Head.",
    "department_kind": DepartmentKind.OPERATIONS,
    "feedback_form": OPERATIONS_ASSESSMENT["name"],
    "stages": [
        # Same as the therapist pipeline: verification IS the first stage.
        (
            20, "Recruiter verification", StageKind.HR_VERIFICATION, RoleCode.RECRUITER,
            [Decision.VERIFY, Decision.REQUEST_INFO, Decision.SCREEN_OUT], False, None,
        ),
        (
            30, "CRE interview", StageKind.INTERVIEW, RoleCode.CRE,
            [Decision.PASS, Decision.RECOMMEND_REJECT], True, OPERATIONS_ASSESSMENT["name"],
        ),
        (
            40, "Operations Manager interview", StageKind.INTERVIEW, RoleCode.OPERATIONS_MANAGER,
            [Decision.PASS, Decision.RECOMMEND_REJECT], True, OPERATIONS_ASSESSMENT["name"],
        ),
        (
            50, "Operational Head recommendation", StageKind.DEPARTMENT_DECISION,
            RoleCode.OPERATIONAL_HEAD,
            [Decision.RECOMMEND_SELECT, Decision.RECOMMEND_REJECT], False, None,
        ),
        (
            60, "HR Head final decision", StageKind.HR_FINAL_DECISION, RoleCode.HR_HEAD,
            [Decision.SELECT, Decision.REJECT], False, None,
        ),
        (70, "Offer", StageKind.OFFER, RoleCode.HR_HEAD, [], False, None),
        (80, "Onboarding", StageKind.ONBOARDING, RoleCode.HR_MANAGER, [], False, None),
        (90, "Hired", StageKind.TERMINAL, None, [], False, None),
        (95, "Rejected", StageKind.TERMINAL, None, [], False, None),
    ],
    "transitions": [
        (20, Decision.VERIFY, 30),
        (20, Decision.REQUEST_INFO, 20),
        (20, Decision.SCREEN_OUT, 95),
        (30, Decision.PASS, 40),
        (30, Decision.RECOMMEND_REJECT, 60),
        (40, Decision.PASS, 50),
        (40, Decision.RECOMMEND_REJECT, 60),
        (50, Decision.RECOMMEND_SELECT, 60),
        (50, Decision.RECOMMEND_REJECT, 60),
        (60, Decision.SELECT, 70),
        (60, Decision.REJECT, 95),
        (70, Decision.OFFER_ACCEPTED, 80),
        (70, Decision.OFFER_DECLINED, 95),
        (80, Decision.PASS, 90),
    ],
    "final_stage_order": 60,
    "terminal_won_order": 90,
    "terminal_lost_order": 95,
}

ALL_WORKFLOWS = (THERAPIST_WORKFLOW, OFFICE_BOY_WORKFLOW)
ALL_FORMS = (CLINICAL_ASSESSMENT, OPERATIONS_ASSESSMENT)


# ---------------------------------------------------------------------------
# Builder — one function, every workflow
# ---------------------------------------------------------------------------


@transaction.atomic
def build_feedback_form(spec: dict) -> FeedbackForm:
    form, _ = org_scoped(FeedbackForm).update_or_create(
        name=spec["name"], defaults={"description": spec.get("description", "")}
    )
    for index, (key, label, kind, required) in enumerate(spec["fields"]):
        org_scoped(FeedbackField).update_or_create(
            form=form,
            key=key,
            defaults={
                "label": label,
                "kind": kind,
                "order": index * 10,
                "is_required": required,
            },
        )
    return form


@transaction.atomic
def build_workflow(spec: dict) -> HiringWorkflow:
    """
    Construct a workflow from a declarative spec.

    Notice there is nothing job-title-specific in this function. It is the same
    code for Therapist, Office Boy, and anything added later.
    """
    from apps.accounts.models import Role

    workflow, _ = org_scoped(HiringWorkflow).update_or_create(
        name=spec["name"],
        defaults={
            "description": spec.get("description", ""),
            "department_kind": spec.get("department_kind", ""),
            "is_published": True,
        },
    )

    # Scoped: unscoped, a stage's responsible_role could be another
    # organization's Role -- and `Role.code` is unique only per
    # organization, so the wrong one matches silently.
    roles = {r.code: r for r in org_scoped(Role)}
    forms = {f.name: f for f in org_scoped(FeedbackForm)}

    stages: dict[int, WorkflowStage] = {}
    for order, name, kind, role_code, decisions, needs_interview, form_name in spec["stages"]:
        stage, _ = org_scoped(WorkflowStage).update_or_create(
            workflow=workflow,
            order=order,
            defaults={
                "name": name,
                "kind": kind,
                "responsible_role": roles.get(role_code) if role_code else None,
                "allowed_decisions": list(decisions),
                "requires_interview": needs_interview,
                "requires_feedback": needs_interview,
                "feedback_form": forms.get(form_name) if form_name else None,
                "is_final_hr_decision": order == spec["final_stage_order"],
                "is_terminal": kind == StageKind.TERMINAL,
                "is_won": order == spec["terminal_won_order"],
                "is_mandatory": True,
            },
        )
        stages[order] = stage

    for from_order, decision, to_order in spec["transitions"]:
        org_scoped(StageTransition).update_or_create(
            from_stage=stages[from_order],
            on_decision=decision,
            defaults={"to_stage": stages[to_order]},
        )

    return workflow


@transaction.atomic
def seed_workflows() -> list[HiringWorkflow]:
    """Idempotent. Safe on every deploy."""
    for form_spec in ALL_FORMS:
        build_feedback_form(form_spec)
    return [build_workflow(spec) for spec in ALL_WORKFLOWS]
