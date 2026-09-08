"""
Recruitment over HTTP.

The service tests prove the rules hold when called correctly. These prove they
hold when called INCORRECTLY, by a real client with a real token, hitting the
route directly — which is the only threat model that matters. A UI that hides
the Reject button is not a control.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.recruitment.models import ApplicationStatus, Interview
from apps.workflows.models import Decision

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"
APPLICATIONS = "/api/v1/applications/"
INTERVIEWS = "/api/v1/interviews/"
JOBS = "/api/v1/jobs/"
OFFERS = "/api/v1/offers/"
WORKFLOWS = "/api/v1/workflows/"

REASON = "The candidate does not meet the clinical requirements for this role."


def _auth(api, user):
    token = api.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


@pytest.fixture
def application(therapist_job, make_application):
    return make_application(therapist_job)


# ==================================================== workflow configuration


def test_the_workflows_are_visible_as_data(api, staff, workflows):
    """
    The pipeline is inspectable configuration, not hidden code.

    A client can read every stage, every allowed decision and every transition
    without knowing anything about the job title.
    """
    _auth(api, staff["hr_head"].user)
    listing = api.get(WORKFLOWS)
    assert listing.status_code == 200

    results = listing.data["data"]
    assert len(results) == 2

    detail = api.get(f"{WORKFLOWS}{results[0]['id']}/")
    assert detail.status_code == 200
    assert len(detail.data["stages"]) == 9
    assert any(stage["transitions"] for stage in detail.data["stages"])


def test_an_interviewer_reads_their_own_pipeline_and_nothing_more(
    api, staff, therapist_job, office_boy_job, make_application
):
    """
    The CRE (interviewer block only) can open the job they are booked to
    interview on, and the reference lists its posting points at — but not
    other jobs, and not offers.
    """
    from apps.recruitment.services.interviews import schedule_interview

    app = make_application(office_boy_job)
    stage = office_boy_job.workflow.stages.get(order=30)  # CRE interview
    app.current_stage = stage
    app.is_verified = True
    app.save(update_fields=["current_stage", "is_verified", "updated_at"])
    schedule_interview(
        application=app, stage=stage, interviewer=staff["cre"],
        actor=staff["hr_head"].user,
        scheduled_at=timezone.now() + dt.timedelta(days=1),
    )

    _auth(api, staff["cre"].user)
    # Their job: readable, list and detail.
    assert api.get(f"{JOBS}{office_boy_job.pk}/").status_code == 200
    listed = api.get(JOBS, {"page_size": "200"})
    assert listed.status_code == 200
    assert {row["id"] for row in listed.data["data"]} == {str(office_boy_job.pk)}
    # A job they hold no interview on: invisible, not merely forbidden.
    assert api.get(f"{JOBS}{therapist_job.pk}/").status_code == 404
    # Reference data behind the posting: readable.
    assert api.get("/api/v1/designations/").status_code == 200
    assert api.get("/api/v1/locations/").status_code == 200
    # Offers remain out of reach.
    assert api.get(OFFERS).status_code == 403


# ==================================================== stage movement


def test_the_recruiter_advances_an_application(api, staff, application):
    # The pipeline opens at Recruiter verification, so the recruiter's first
    # move over HTTP is the Verify itself.
    _auth(api, staff["recruiter"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/advance/", {"decision": Decision.VERIFY}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["from_stage"] == "Recruiter verification"


def test_the_advance_route_refuses_a_terminal_decision(api, staff, application, at_stage):
    """
    Route separation is a control, not tidiness.

    `advance` is gated on APPLICATION/EDIT, which many roles hold. If a
    terminal decision could be smuggled through it, the REJECT gate on the
    dedicated route would be worthless.
    """
    at_stage(application, 60)
    _auth(api, staff["hr_manager"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/advance/",
        {"decision": Decision.REJECT, "rationale": REASON},
        format="json",
    )
    assert response.status_code == 400
    assert "/reject/" in str(response.data)


def test_the_advance_route_refuses_a_recommendation(api, staff, application, at_stage):
    at_stage(application, 50)
    _auth(api, staff["hr_manager"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/advance/",
        {"decision": Decision.RECOMMEND_SELECT},
        format="json",
    )
    assert response.status_code == 400
    assert "/recommend/" in str(response.data)


def test_a_department_head_recommends_over_http(api, staff, application, at_stage):
    at_stage(application, 50)
    _auth(api, staff["medical_director"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/recommend/",
        {"decision": Decision.RECOMMEND_REJECT, "rationale": REASON},
        format="json",
    )
    assert response.status_code == 200, response.data

    application.refresh_from_db()
    # A recommendation routes to HR — it does not reject.
    assert application.status == ApplicationStatus.ACTIVE
    assert application.current_stage.is_final_hr_decision


def test_a_recommendation_without_a_rationale_is_refused(api, staff, application, at_stage):
    at_stage(application, 50)
    _auth(api, staff["medical_director"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/recommend/",
        {"decision": Decision.RECOMMEND_REJECT, "rationale": "no"},
        format="json",
    )
    assert response.status_code == 400
    assert "rationale" in response.data["error"]["details"]


# ==================================================== the rejection gate


def test_only_hr_head_can_reject_over_http(api, staff, application, at_stage):
    at_stage(application, 60)
    _auth(api, staff["hr_head"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/reject/", {"reason": REASON}, format="json"
    )
    assert response.status_code == 200, response.data

    application.refresh_from_db()
    assert application.status == ApplicationStatus.REJECTED
    assert application.rejection.reason == REASON


@pytest.mark.parametrize(
    "role_code",
    ["medical_director", "operational_head", "recruiter", "hr_manager", "senior_doctor"],
)
def test_nobody_but_hr_head_reaches_the_reject_route(
    api, staff, application, at_stage, role_code
):
    """
    The cutover gate: a direct API call must not bypass the UI restriction.

    Every one of these roles can see the application. None holds
    APPLICATION/REJECT, so the route refuses before the service is entered.
    """
    at_stage(application, 60)
    _auth(api, staff[role_code].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/reject/", {"reason": REASON}, format="json"
    )
    assert response.status_code == 403, f"{role_code} reached the rejection route"

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE


def test_admin_cannot_reject_but_can_override(api, admin_user, staff, application, at_stage):
    """
    Admin's authority is the exception path, deliberately not the normal one.

    403 on /reject/ and 201 on /override/ in the same test, because the
    distinction is the whole design and testing them apart would let one drift.
    """
    at_stage(application, 60)
    _auth(api, admin_user)

    rejected = api.post(
        f"{APPLICATIONS}{application.pk}/reject/", {"reason": REASON}, format="json"
    )
    assert rejected.status_code == 403

    overridden = api.post(
        f"{APPLICATIONS}{application.pk}/override/",
        {
            "new_status": ApplicationStatus.REJECTED,
            "reason": "Closing this application administratively at the candidate's request.",
        },
        format="json",
    )
    assert overridden.status_code == 201, overridden.data

    application.refresh_from_db()
    assert application.status == ApplicationStatus.REJECTED
    # Recorded as an override, NOT as an HR rejection.
    assert not hasattr(application, "rejection") or application.rejection is None


def test_reopening_over_http_leaves_the_candidate_workable(
    api, admin_user, staff, application, at_stage
):
    """
    The end-to-end shape of the fix, as a client sees it.

    HR Head rejects; Admin reopens; the application comes back with a
    non-terminal stage AND a non-empty `allowed_decisions`, which is what the
    UI reads to decide whether there is anything to do. Before the fix this
    returned an active application with an empty decision list.
    """
    at_stage(application, 60)

    _auth(api, staff["hr_head"].user)
    assert (
        api.post(f"{APPLICATIONS}{application.pk}/reject/", {"reason": REASON}, format="json").status_code
        == 200
    )

    closed = api.get(f"{APPLICATIONS}{application.pk}/")
    assert closed.data["status"] == ApplicationStatus.REJECTED
    assert closed.data["allowed_decisions"] == []

    _auth(api, admin_user)
    reopened = api.post(
        f"{APPLICATIONS}{application.pk}/override/",
        {
            "new_status": ApplicationStatus.ACTIVE,
            "reason": "Requisition reinstated after the department corrected its forecast.",
        },
        format="json",
    )
    assert reopened.status_code == 201, reopened.data
    assert reopened.data["previous_stage"] is not None
    assert reopened.data["new_stage"] != reopened.data["previous_stage"]

    live = api.get(f"{APPLICATIONS}{application.pk}/")
    assert live.data["status"] == ApplicationStatus.ACTIVE
    assert live.data["allowed_decisions"], "a reopened application must offer decisions again"

    # And HR Head can genuinely act on it again.
    _auth(api, staff["hr_head"].user)
    decided = api.post(f"{APPLICATIONS}{application.pk}/select/", {}, format="json")
    assert decided.status_code == 200, decided.data

    application.refresh_from_db()
    assert application.status == ApplicationStatus.SELECTED
    # The original rejection is still on the record, flagged rather than erased.
    assert application.rejection.reason == REASON
    assert application.rejection.is_overridden is True


def test_hr_head_cannot_reach_the_override_route(api, staff, application, at_stage):
    at_stage(application, 60)
    _auth(api, staff["hr_head"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/override/",
        {"new_status": ApplicationStatus.ACTIVE, "reason": REASON},
        format="json",
    )
    assert response.status_code == 403


def test_a_rejection_without_a_reason_is_refused_at_the_api(
    api, staff, application, at_stage
):
    """Layer 2 of the three-layer reason floor: serializer, service, database."""
    at_stage(application, 60)
    _auth(api, staff["hr_head"].user)

    for bad in ({}, {"reason": ""}, {"reason": "too short"}):
        response = api.post(f"{APPLICATIONS}{application.pk}/reject/", bad, format="json")
        assert response.status_code == 400, bad

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE


# ==================================================== department isolation


def test_a_department_head_sees_only_their_own_pipeline(
    api, staff, therapist_job, office_boy_job, make_application
):
    """
    A Medical Director must see medical applications and not operations ones.

    Recruitment rows hang off a job, not a person, so this scoping is done by
    the recruitment viewsets rather than the generic engine — which makes it
    exactly the kind of thing that silently returns everything if wrong.
    """
    make_application(therapist_job, email="med@example.test")
    make_application(office_boy_job, email="ops@example.test")

    _auth(api, staff["medical_director"].user)
    response = api.get(APPLICATIONS)
    assert response.status_code == 200

    departments = {row["department_name"] for row in response.data["data"]}
    assert departments == {therapist_job.department.name}


def test_a_department_head_cannot_act_on_another_department(
    api, staff, office_boy_job, make_application, at_stage
):
    """Seeing nothing is not enough — reaching it by id must also fail."""
    application = at_stage(make_application(office_boy_job), 50)
    _auth(api, staff["medical_director"].user)

    response = api.post(
        f"{APPLICATIONS}{application.pk}/recommend/",
        {"decision": Decision.RECOMMEND_SELECT, "rationale": REASON},
        format="json",
    )
    # 404 rather than 403: out-of-scope rows must not confirm their existence.
    assert response.status_code == 404


def test_hr_head_sees_every_department(
    api, staff, therapist_job, office_boy_job, make_application
):
    make_application(therapist_job, email="med2@example.test")
    make_application(office_boy_job, email="ops2@example.test")

    _auth(api, staff["hr_head"].user)
    response = api.get(APPLICATIONS)
    assert len(response.data["data"]) == 2


def test_the_hr_queue_lists_only_applications_awaiting_a_final_decision(
    api, staff, therapist_job, make_application, at_stage
):
    waiting = at_stage(make_application(therapist_job, email="w@example.test"), 60)
    at_stage(make_application(therapist_job, email="e@example.test"), 30)

    _auth(api, staff["hr_head"].user)
    response = api.get(f"{APPLICATIONS}pending-hr-decision/")
    assert response.status_code == 200

    ids = {row["id"] for row in response.data["data"]}
    assert ids == {str(waiting.pk)}


# ==================================================== interviews


def test_scheduling_over_http_refuses_a_double_booking(
    api, staff, therapist_job, make_application, at_stage
):
    stage = therapist_job.workflow.stages.get(order=30)
    first = at_stage(make_application(therapist_job, email="s1@example.test"), 30)
    second = at_stage(make_application(therapist_job, email="s2@example.test"), 30)
    slot = timezone.now() + dt.timedelta(days=5)

    _auth(api, staff["hr_head"].user)
    payload = {
        "application": str(first.pk),
        "stage": str(stage.pk),
        "interviewer": str(staff["clinic_doctor"].pk),
        "scheduled_at": slot.isoformat(),
        "duration_minutes": 45,
    }
    assert api.post(INTERVIEWS, payload, format="json").status_code == 201

    clash = api.post(
        INTERVIEWS,
        {
            **payload,
            "application": str(second.pk),
            "scheduled_at": (slot + dt.timedelta(minutes=20)).isoformat(),
        },
        format="json",
    )
    # A readable 400, not a 500 from the database constraint underneath.
    assert clash.status_code == 400, clash.data
    assert "already has an interview" in str(clash.data)
    assert Interview.objects.count() == 1


def test_the_conflict_check_endpoint_answers_before_submission(
    api, staff, therapist_job, make_application, at_stage
):
    stage = therapist_job.workflow.stages.get(order=30)
    application = at_stage(make_application(therapist_job), 30)
    slot = timezone.now() + dt.timedelta(days=6)

    _auth(api, staff["hr_head"].user)
    api.post(
        INTERVIEWS,
        {
            "application": str(application.pk),
            "stage": str(stage.pk),
            "interviewer": str(staff["clinic_doctor"].pk),
            "scheduled_at": slot.isoformat(),
            "duration_minutes": 45,
        },
        format="json",
    )

    busy = api.get(
        f"{INTERVIEWS}check-conflict/",
        {
            "interviewer": str(staff["clinic_doctor"].pk),
            "start": (slot + dt.timedelta(minutes=10)).isoformat(),
            "end": (slot + dt.timedelta(minutes=55)).isoformat(),
        },
    )
    assert busy.status_code == 200
    assert busy.data["conflict"] is True

    free = api.get(
        f"{INTERVIEWS}check-conflict/",
        {
            "interviewer": str(staff["clinic_doctor"].pk),
            "start": (slot + dt.timedelta(hours=4)).isoformat(),
            "end": (slot + dt.timedelta(hours=5)).isoformat(),
        },
    )
    assert free.data["conflict"] is False


def test_the_eligible_interviewer_endpoint_answers_from_the_stage(
    api, staff, therapist_job
):
    """
    The picker's source of truth: the stage's role decides, not the caller's
    view of the directory — and when the pool is empty it says why.
    """
    from apps.employees.models import EmployeeStatus

    stage = therapist_job.workflow.stages.get(order=40)  # Senior Doctor round

    _auth(api, staff["hr_head"].user)
    ok = api.get(f"{INTERVIEWS}eligible-interviewers/", {"stage": str(stage.pk)})
    assert ok.status_code == 200
    assert [row["id"] for row in ok.data["data"]] == [str(staff["senior_doctor"].pk)]
    assert ok.data["problem"] == ""
    assert ok.data["role_code"] == "senior_doctor"

    # The only holder leaves: the pool empties and names the fix.
    staff["senior_doctor"].status = EmployeeStatus.TERMINATED
    staff["senior_doctor"].save(update_fields=["status"])

    gone = api.get(f"{INTERVIEWS}eligible-interviewers/", {"stage": str(stage.pk)})
    assert gone.data["data"] == []
    assert "has left the organisation" in gone.data["problem"]


def test_only_a_scheduler_may_ask_who_is_eligible(api, staff, therapist_job):
    """It rides INTERVIEW/CREATE — the authority to schedule."""
    stage = therapist_job.workflow.stages.get(order=40)

    _auth(api, staff["therapist"].user)
    denied = api.get(f"{INTERVIEWS}eligible-interviewers/", {"stage": str(stage.pk)})
    assert denied.status_code == 403


def test_an_interviewer_sees_only_their_own_interviews(
    api, staff, therapist_job, make_application, at_stage, completed_interview
):
    """Layer-4 'assigned candidates only', concretely."""
    first = at_stage(make_application(therapist_job, email="i1@example.test"), 30)
    second = at_stage(make_application(therapist_job, email="i2@example.test"), 40)
    completed_interview(first, 30, "clinic_doctor")
    completed_interview(second, 40, "senior_doctor")

    _auth(api, staff["clinic_doctor"].user)
    response = api.get(INTERVIEWS)
    assert response.status_code == 200

    interviewers = {str(row["interviewer"]) for row in response.data["data"]}
    assert interviewers == {str(staff["clinic_doctor"].pk)}


def test_feedback_is_submitted_over_http_by_the_assigned_interviewer(
    api, staff, therapist_job, make_application, at_stage
):
    stage = therapist_job.workflow.stages.get(order=30)
    application = at_stage(make_application(therapist_job), 30)
    slot = timezone.now() + dt.timedelta(days=7)

    _auth(api, staff["hr_head"].user)
    created = api.post(
        INTERVIEWS,
        {
            "application": str(application.pk),
            "stage": str(stage.pk),
            "interviewer": str(staff["clinic_doctor"].pk),
            "scheduled_at": slot.isoformat(),
        },
        format="json",
    )
    interview_id = created.data["id"]

    answers = {f.key: 4 for f in stage.feedback_form.fields.filter(is_required=True)}

    _auth(api, staff["clinic_doctor"].user)
    response = api.post(
        f"{INTERVIEWS}{interview_id}/feedback/",
        {"answers": answers, "recommendation": "hire", "overall_rating": 4},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["recommendation"] == "hire"


# ==================================================== offers and conversion


def test_the_full_pipeline_over_http(
    api, staff, therapist_job, make_application, drive_to_selection, org
):
    """
    Selection → offer → acceptance → employee, entirely over HTTP.

    The pipeline itself is driven by the service fixture; from selection
    onwards every step is a real request, because that is where an unguarded
    route would show up.
    """
    from core.access.catalog import Layer

    application = drive_to_selection(
        make_application(therapist_job),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )

    _auth(api, staff["hr_head"].user)

    created = api.post(
        OFFERS,
        {
            "application": str(application.pk),
            "offered_ctc": "620000.00",
            "joining_date": "2026-11-02",
            "designation": str(org["designation"].pk),
            "level": str(org["levels"][Layer.STAFF].pk),
            "reporting_manager": str(staff["medical_director"].pk),
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    offer_id = created.data["id"]

    assert api.post(f"{OFFERS}{offer_id}/send/", {}, format="json").status_code == 200
    responded = api.post(
        f"{OFFERS}{offer_id}/respond/", {"accepted": True}, format="json"
    )
    assert responded.status_code == 200

    converted = api.post(
        f"{APPLICATIONS}{application.pk}/convert/",
        {"reporting_manager": str(staff["medical_director"].pk)},
        format="json",
    )
    assert converted.status_code == 201, converted.data
    assert converted.data["employee_code"]
    assert converted.data["role"] == "therapist"

    application.refresh_from_db()
    assert application.status == ApplicationStatus.HIRED


def test_a_recruiter_cannot_create_an_offer_over_http(
    api, staff, therapist_job, make_application, drive_to_selection
):
    application = drive_to_selection(
        make_application(therapist_job),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )

    _auth(api, staff["recruiter"].user)
    response = api.post(
        OFFERS,
        {
            "application": str(application.pk),
            "offered_ctc": "620000.00",
            "joining_date": "2026-11-02",
        },
        format="json",
    )
    assert response.status_code == 403


def test_a_recruiter_cannot_convert_a_candidate_over_http(
    api, staff, therapist_job, make_application, drive_to_selection
):
    """
    Conversion is gated on EMPLOYEE/CREATE, not on recruitment permissions.

    The recruiter runs the whole pipeline and still cannot mint an employee —
    which is the segregation the employee-first architecture exists to keep.
    """
    application = drive_to_selection(
        make_application(therapist_job),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )

    _auth(api, staff["recruiter"].user)
    response = api.post(f"{APPLICATIONS}{application.pk}/convert/", {}, format="json")
    assert response.status_code == 403


# ==================================================== history


def test_the_history_endpoint_returns_the_whole_journey(
    api, staff, therapist_job, make_application, drive_to_selection
):
    application = drive_to_selection(
        make_application(therapist_job),
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )

    _auth(api, staff["hr_head"].user)
    response = api.get(f"{APPLICATIONS}{application.pk}/history/")
    assert response.status_code == 200

    body = response.data
    kinds = {event["kind"] for event in body["events"]}
    assert "stage_changed" in kinds
    assert "feedback_submitted" in kinds
    assert "recommendation" in kinds
    assert "hr_decision" in kinds


def test_an_anonymous_client_reaches_nothing(api, application):
    for url in (APPLICATIONS, JOBS, INTERVIEWS, OFFERS, WORKFLOWS):
        assert api.get(url).status_code == 401, url


def test_an_interviewer_records_their_rounds_pass_over_http(
    api, therapist_job, make_application, at_stage, staff
):
    """
    The /advance/ route demanded APPLICATION/EDIT for every decision, which
    no pure interviewer role holds — so a Clinic Doctor could complete their
    interview and then not record the pass the engine was built to accept
    from them. The engine's per-decision authority is the gate now.
    """
    from tests.recruitment.test_helpers import complete_interview_for

    application = at_stage(make_application(therapist_job), 30)
    complete_interview_for(application, 30, "clinic_doctor", staff)

    _auth(api, staff["clinic_doctor"].user)
    response = api.post(
        f"{APPLICATIONS}{application.pk}/advance/", {"decision": "pass"}, format="json"
    )
    assert response.status_code == 200, response.data

    # The engine still refuses anyone the workflow did not put in the chair —
    # the route's weaker gate opened nothing else.
    application2 = at_stage(make_application(therapist_job, email="x2@example.test"), 30)
    complete_interview_for(application2, 30, "clinic_doctor", staff)
    _auth(api, staff["senior_doctor"].user)
    refused = api.post(
        f"{APPLICATIONS}{application2.pk}/advance/", {"decision": "pass"}, format="json"
    )
    # 404: SELF-scoped, they cannot even see an application they are not
    # booked on. (Were they booked, the engine would answer 400 with
    # "may only be actioned by".) Either way: refused.
    assert refused.status_code in (400, 404)
