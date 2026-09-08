"""
Offers, candidate-to-employee conversion, and the administrative override.

The conversion tests matter most: they are what prove recruitment did not grow
a second, weaker employee-creation path alongside the one the employee module
already guards.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError

from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    DecisionOverride,
    Offer,
    OfferStatus,
)
from apps.recruitment.services.hiring import (
    convert_to_employee,
    create_offer,
    override_decision,
    record_offer_response,
    send_offer,
)
from core.access.catalog import DepartmentKind
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

JOINING = dt.date(2026, 10, 1)


@pytest.fixture
def selected(therapist_job, make_application, drive_to_selection, staff):
    """A therapist application carried by the engine all the way to SELECTED."""
    application = make_application(therapist_job)
    return drive_to_selection(
        application,
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )


@pytest.fixture
def accepted_offer(selected, staff, org):
    from core.access.catalog import Layer

    offer = create_offer(
        application=selected,
        actor=staff["hr_head"].user,
        offered_ctc="600000.00",
        joining_date=JOINING,
        designation=org["designation"],
        level=org["levels"][Layer.STAFF],
        reporting_manager=staff["medical_director"],
    )
    send_offer(offer=offer, actor=staff["hr_head"].user)
    record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)
    selected.refresh_from_db()
    return offer


# ============================================================ offers


def test_an_offer_requires_a_selected_candidate(
    therapist_job, make_application, at_stage, staff
):
    """No shortcut from mid-pipeline straight to an offer."""
    application = at_stage(make_application(therapist_job), 30)

    with pytest.raises(ValidationError) as exc:
        create_offer(
            application=application, actor=staff["hr_head"].user,
            offered_ctc="600000.00", joining_date=JOINING,
        )
    assert "selected first" in str(exc.value)


def test_the_recruiter_cannot_create_an_offer(selected, staff):
    """
    The recruiter runs the pipeline but does not commit the company to terms.

    OFFER/CREATE is HR Head's; the recruiter holds VIEW only.
    """
    with pytest.raises(AccessDenied):
        create_offer(
            application=selected, actor=staff["recruiter"].user,
            offered_ctc="600000.00", joining_date=JOINING,
        )


def test_hr_head_creates_sends_and_records_the_response(selected, staff):
    offer = create_offer(
        application=selected, actor=staff["hr_head"].user,
        offered_ctc="600000.00", joining_date=JOINING,
    )
    assert offer.status == OfferStatus.DRAFT

    send_offer(offer=offer, actor=staff["hr_head"].user)
    offer.refresh_from_db()
    selected.refresh_from_db()
    assert offer.status == OfferStatus.SENT
    assert offer.sent_at is not None
    assert selected.status == ApplicationStatus.OFFER_SENT

    record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)
    offer.refresh_from_db()
    selected.refresh_from_db()
    assert offer.status == OfferStatus.ACCEPTED
    assert selected.status == ApplicationStatus.OFFER_ACCEPTED


def test_only_one_offer_per_application(selected, staff):
    create_offer(
        application=selected, actor=staff["hr_head"].user,
        offered_ctc="600000.00", joining_date=JOINING,
    )
    with pytest.raises(ValidationError) as exc:
        create_offer(
            application=selected, actor=staff["hr_head"].user,
            offered_ctc="700000.00", joining_date=JOINING,
        )
    assert "already exists" in str(exc.value)


def test_an_unsent_offer_cannot_be_responded_to(selected, staff):
    offer = create_offer(
        application=selected, actor=staff["hr_head"].user,
        offered_ctc="600000.00", joining_date=JOINING,
    )
    with pytest.raises(ValidationError) as exc:
        record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)
    assert "sent offer" in str(exc.value)


def test_a_declined_offer_starts_the_retention_clock(selected, staff):
    """
    A decline ends the candidacy, so the DPDP retention clock must start —
    otherwise declined candidates are held indefinitely.
    """
    offer = create_offer(
        application=selected, actor=staff["hr_head"].user,
        offered_ctc="600000.00", joining_date=JOINING,
    )
    send_offer(offer=offer, actor=staff["hr_head"].user)
    record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=False)

    selected.refresh_from_db()
    selected.candidate.refresh_from_db()
    assert selected.status == ApplicationStatus.OFFER_DECLINED
    assert selected.candidate.final_decision_at is not None
    assert selected.candidate.retention_until is not None


# ============================================================ conversion


def test_conversion_requires_an_accepted_offer(selected, staff):
    with pytest.raises(ValidationError) as exc:
        convert_to_employee(application=selected, actor=staff["hr_head"].user)
    assert "accepted offer" in str(exc.value)


def test_candidate_becomes_an_employee_atomically(accepted_offer, selected, staff):
    result = convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )

    employee = result.employee
    candidate = selected.candidate

    # The person, the login and the role all exist and are linked.
    assert employee.user is not None
    assert employee.user.email == candidate.email
    assert result.role.code == "therapist"
    assert employee.user.user_roles.filter(role__code="therapist").exists()

    # Placement came from the job and the offer, not from free text.
    assert employee.department_id == selected.job_opening.department_id
    assert employee.date_of_joining == JOINING
    assert employee.reporting_manager_id == staff["medical_director"].pk

    selected.refresh_from_db()
    assert selected.status == ApplicationStatus.HIRED


def test_conversion_records_the_candidate_it_came_from(accepted_offer, selected, staff):
    """
    `created_from_candidate` was deferred when Employee was built because
    Candidate did not exist yet. This is the link being closed — and it is also
    what permanently exempts a hired candidate from the retention purge.
    """
    result = convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    result.employee.refresh_from_db()
    assert result.employee.created_from_candidate_id == selected.candidate_id


def test_conversion_stamps_the_final_decision(accepted_offer, selected, staff):
    convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    selected.candidate.refresh_from_db()
    assert selected.candidate.final_decision_at is not None


def _manager_without_a_role(org):
    """
    Someone with no login and no role. Approvals routed to them would go
    nowhere, so the hierarchy rules refuse them as a manager.

    (A same-layer PEER is deliberately NOT used here: peer reporting is valid
    now, so it would no longer test anything.)
    """
    from apps.employees.models import Employee

    return Employee.objects.create(
        employee_code="EMP08999", first_name="No", last_name="Login",
        department=org["departments"][DepartmentKind.MEDICAL],
        location=org["location"], date_of_joining=dt.date(2025, 1, 1),
    )


def test_conversion_obeys_the_hierarchy_rules(accepted_offer, selected, staff, org):
    """
    Conversion delegates to `create_employee`, so an invalid reporting manager
    is refused here exactly as it would be on the manual path. If recruitment
    had written its own creation code, this would pass.
    """
    with pytest.raises(ValidationError) as exc:
        convert_to_employee(
            application=selected, actor=staff["hr_head"].user,
            reporting_manager=_manager_without_a_role(org),
        )
    assert "reporting_manager" in str(exc.value.message_dict).lower()


def test_a_failed_conversion_leaves_nothing_behind(accepted_offer, selected, staff, org):
    """
    The atomicity proof.

    A conversion that fails after the Employee row is written must leave no
    employee, no user and no role — and the application must remain
    OFFER_ACCEPTED, still convertible once the problem is fixed.
    """
    from apps.accounts.models import User
    from apps.employees.models import Employee

    employees_before = Employee.objects.count()
    users_before = User.objects.count()

    with pytest.raises(ValidationError):
        convert_to_employee(
            application=selected, actor=staff["hr_head"].user,
            reporting_manager=_manager_without_a_role(org),
        )

    # The invalid manager above is itself a row; count from after it exists.
    assert Employee.objects.count() == employees_before + 1
    assert User.objects.count() == users_before
    selected.refresh_from_db()
    assert selected.status == ApplicationStatus.OFFER_ACCEPTED


def test_the_rollback_test_is_meaningful(accepted_offer, selected, staff, monkeypatch):
    """
    Guards the test above.

    If conversion ever started failing BEFORE its first write, the rollback
    test would pass without ever exercising a rollback. Here the failure is
    injected after the Employee row exists, so the assertion has teeth.
    """
    from apps.accounts.models import User
    from apps.employees.models import Employee
    from apps.recruitment.services import hiring

    employees_before = Employee.objects.count()
    users_before = User.objects.count()
    seen = {}

    real_save = Employee.save

    def explode(self, *args, **kwargs):
        real_save(self, *args, **kwargs)
        if "created_from_candidate" in (kwargs.get("update_fields") or ()):
            # Row counts AT THE MOMENT OF FAILURE — proof that writes happened.
            seen["employees"] = Employee.objects.count()
            seen["users"] = User.objects.count()
            raise ValidationError({"conversion": "injected failure"})

    monkeypatch.setattr(Employee, "save", explode)

    with pytest.raises(ValidationError):
        hiring.convert_to_employee(
            application=selected, actor=staff["hr_head"].user,
            reporting_manager=staff["medical_director"],
        )

    monkeypatch.undo()

    assert seen["employees"] == employees_before + 1, "nothing was written; the test proves nothing"
    assert seen["users"] == users_before + 1
    assert Employee.objects.count() == employees_before
    assert User.objects.count() == users_before


def test_conversion_writes_a_history_event(accepted_offer, selected, staff):
    convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    event = selected.events.filter(kind=ApplicationEvent.Kind.CONVERTED).first()
    assert event is not None
    assert event.detail["employee_code"]


# ============================================================ admin override


def _reject(application, staff):
    from apps.recruitment.services.engine import record_decision
    from apps.workflows.models import Decision

    record_decision(
        application=application, actor=staff["hr_head"].user, decision=Decision.REJECT,
        rationale="Not a fit for the clinical requirements of this role.",
    )
    application.refresh_from_db()
    return application


def test_only_admin_may_override(selected, staff):
    """HR Head owns rejection; the exceptional override is Admin's alone."""
    with pytest.raises(AccessDenied):
        override_decision(
            application=selected, actor=staff["hr_head"].user,
            new_status=ApplicationStatus.ACTIVE,
            reason="Reversing an earlier decision after fresh information.",
        )


def test_a_department_head_may_not_override(selected, staff):
    with pytest.raises(AccessDenied):
        override_decision(
            application=selected, actor=staff["medical_director"].user,
            new_status=ApplicationStatus.ACTIVE,
            reason="Reversing an earlier decision after fresh information.",
        )


def test_an_override_requires_a_substantial_reason(selected, admin_user):
    with pytest.raises(ValidationError) as exc:
        override_decision(
            application=selected, actor=admin_user,
            new_status=ApplicationStatus.ACTIVE, reason="mistake",
        )
    assert "at least" in str(exc.value)


def test_the_override_is_recorded_separately_from_the_rejection(
    therapist_job, make_application, at_stage, staff, admin_user
):
    """
    An override must never be mistakable for a normal HR decision.

    It writes its own row, in its own table, and flags the rejection it
    reverses rather than editing it away.
    """
    application = at_stage(make_application(therapist_job), 60)
    _reject(application, staff)
    assert application.status == ApplicationStatus.REJECTED

    reason = "Rejection reversed: the department confirmed the credentials were misread."
    override = override_decision(
        application=application, actor=admin_user,
        new_status=ApplicationStatus.ACTIVE, reason=reason,
    )

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE
    assert override.previous_status == ApplicationStatus.REJECTED
    assert override.new_status == ApplicationStatus.ACTIVE
    assert override.reason == reason
    assert override.overridden_by_id == admin_user.pk

    # The original rejection still exists, flagged — not deleted or rewritten.
    application.rejection.refresh_from_db()
    assert application.rejection.is_overridden is True
    assert application.rejection.reason  # the original reason survives


def test_an_override_is_audited(therapist_job, make_application, at_stage, staff, admin_user):
    from apps.audit.models import AuditAction, AuditLog

    application = at_stage(make_application(therapist_job), 60)
    _reject(application, staff)

    override_decision(
        application=application, actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason="Rejection reversed after the department corrected its assessment.",
    )

    entry = AuditLog.objects.filter(
        action=AuditAction.OVERRIDE, entity_id=str(application.pk)
    ).first()
    assert entry is not None
    assert entry.actor_id == admin_user.pk
    assert entry.after["event"] == "administrative_override"
    # The prior state lives in `before`, so the audit viewer's previous/new
    # column pair renders it. Same facts, correct columns.
    assert entry.before["status"] == ApplicationStatus.REJECTED
    assert entry.after["status"] == ApplicationStatus.ACTIVE


def test_an_override_appears_in_the_candidate_history(
    therapist_job, make_application, at_stage, staff, admin_user
):
    application = at_stage(make_application(therapist_job), 60)
    _reject(application, staff)
    override_decision(
        application=application, actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason="Rejection reversed after the department corrected its assessment.",
    )

    event = application.events.filter(kind=ApplicationEvent.Kind.OVERRIDE).first()
    assert event is not None
    assert event.detail["new_status"] == ApplicationStatus.ACTIVE


def test_an_override_to_an_unknown_status_is_refused(selected, admin_user):
    with pytest.raises(ValidationError) as exc:
        override_decision(
            application=selected, actor=admin_user, new_status="promoted_to_ceo",
            reason="Attempting to set a status the workflow does not define.",
        )
    assert "Unknown status" in str(exc.value)


def test_the_database_refuses_a_short_override_reason(selected, admin_user):
    """
    The reason floor is not only a service check.

    A short reason must fail even when written straight through the ORM,
    because that is what a future code path or a manual fix would do.
    """
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():
            DecisionOverride.objects.create(
                application=selected, overridden_by=admin_user,
                previous_status=ApplicationStatus.ACTIVE,
                new_status=ApplicationStatus.REJECTED,
                reason="too short",
            )
    assert "ck_override_reason_minimum_length" in str(exc.value)


def test_a_candidate_with_no_email_cannot_be_converted(accepted_offer, selected, staff):
    """
    Candidates may be phone-only; employees may not.

    An employee's login IS their email, and `UserManager._create_user` raises
    ValueError — not ValidationError — on an empty one. Unguarded, a WorkIndia
    import that reached the end of a hiring process would 500 instead of
    telling HR to collect an address first.
    """
    candidate = selected.candidate
    candidate.email = None
    candidate.save(update_fields=["email"])

    with pytest.raises(ValidationError) as exc:
        convert_to_employee(
            application=selected, actor=staff["hr_head"].user,
            reporting_manager=staff["medical_director"],
        )

    assert "email" in str(exc.value).lower()


# ============================================== the onboarding form's overrides


def test_onboarding_overrides_reach_the_employee(accepted_offer, selected, staff, org):
    """
    HR Head verifies the joiner's details on the onboarding form: name, work
    email, phone, location and joining date may all be corrected before the
    account is minted, and each lands on the employee and the login.
    """
    from apps.organization.models import Location

    goa = Location.objects.create(name="Goa Clinic", code="GOA", city="Panaji", state="GA")
    result = convert_to_employee(
        application=selected,
        actor=staff["hr_head"].user,
        first_name="Asha",
        last_name="Verified",
        email="asha.verified@work.example",
        phone="9111111111",
        location=goa,
        date_of_joining=dt.date(2026, 11, 2),
        reporting_manager=staff["medical_director"],
    )

    employee = result.employee
    assert employee.first_name == "Asha"
    assert employee.last_name == "Verified"
    assert employee.work_email == "asha.verified@work.example"
    assert result.user.email == "asha.verified@work.example"
    assert employee.phone == "9111111111"
    assert employee.location_id == goa.pk
    assert employee.date_of_joining == dt.date(2026, 11, 2)
    # The login is provisioned with a temporary password and must be changed.
    assert result.user.must_change_password is True
    assert result.temporary_password
    # Never stored in clear: the hash validates it, the field does not contain it.
    assert result.user.password != result.temporary_password
    assert result.user.check_password(result.temporary_password)


def test_omitted_onboarding_fields_fall_back_to_the_application(
    accepted_offer, selected, staff
):
    """Nothing supplied → the candidate, job and offer fill every field, as before."""
    candidate = selected.candidate
    result = convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    assert result.employee.first_name == candidate.first_name
    assert result.employee.work_email == candidate.email
    assert result.employee.date_of_joining == JOINING


def test_onboarding_designation_must_belong_to_the_department(
    accepted_offer, selected, staff, org
):
    """
    The department↔designation rule holds at the last step too: an operations
    title cannot be minted onto a medical hire. Enforced by `create_employee`,
    which conversion delegates to — not by a second copy of the rule.
    """
    from apps.organization.models import Designation
    from core.access.catalog import DepartmentKind

    ops_title = Designation.objects.create(
        title="Operations Coordinator",
        department=org["departments"][DepartmentKind.OPERATIONS],
    )
    with pytest.raises(ValidationError) as exc:
        convert_to_employee(
            application=selected,
            actor=staff["hr_head"].user,
            designation=ops_title,
            reporting_manager=staff["medical_director"],
        )
    assert "belongs to another department" in str(exc.value)


def test_conversion_records_the_agreed_ctc_from_the_offer(
    accepted_offer, selected, staff
):
    """The accepted offer's CTC lands on the employee record, informationally."""
    result = convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )
    assert str(result.employee.annual_ctc) == "600000.00"


def test_conversion_splits_company_login_from_the_personal_address(
    accepted_offer, selected, staff
):
    """
    Flow B of the two-email rule: the address the candidate applied with is
    their PERSONAL email; HR's onboarding form supplies the COMPANY email,
    which becomes the one login identifier.
    """
    candidate = selected.candidate
    result = convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        email="new.joiner@company.test",  # the Company Email HR entered
        reporting_manager=staff["medical_director"],
    )
    assert result.user.email == "new.joiner@company.test"
    assert result.employee.work_email == "new.joiner@company.test"
    assert result.employee.personal_email == candidate.email
