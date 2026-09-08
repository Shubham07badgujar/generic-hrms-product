"""
Onboarding: template selection, checklist creation, completion.

The rule under test throughout is that onboarding is CONFIGURATION. The last
test builds a template out of steps this codebase has never heard of and
asserts a hire gets them — which a hard-coded checklist could not do.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError

from apps.onboarding.models import (
    EmployeeOnboarding,
    ItemKind,
    ItemOwner,
    ItemStatus,
    OnboardingItem,
    OnboardingStatus,
    OnboardingTemplate,
    OnboardingTemplateItem,
)
from apps.onboarding.services import (
    complete_item,
    complete_onboarding,
    reopen_item,
    resolve_template,
    start_onboarding,
    waive_item,
)
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db


def _extra_task(onboarding, *, owner=ItemOwner.HR, assigned_to=None, title="Branch orientation walk-through"):
    """
    A plain TASK line added to a live checklist.

    The default template no longer carries any TASK items (the manager
    induction lines were removed by policy, Aug 2026), but the task
    lifecycle — complete, waive, reopen, who-may-tick — still exists for
    organisations that add their own. These tests exercise it on an
    ad-hoc line rather than quietly disappearing with the template rows.
    """
    return OnboardingItem.objects.create(
        onboarding=onboarding,
        title=title,
        kind=ItemKind.TASK,
        owner=owner,
        assigned_to=assigned_to,
        is_mandatory=False,
        order=990,
    )


# ===================================================== creation


def test_a_new_hire_arrives_with_a_checklist(employee):
    """Issued by `create_employee`, inside the same transaction as the hire."""
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    assert onboarding.status == OnboardingStatus.IN_PROGRESS
    assert onboarding.items.count() > 0
    assert onboarding.joining_date == employee.date_of_joining


def test_the_checklist_is_minimal_by_policy(employee):
    """
    The minimal-onboarding policy, pinned.

    Only mandatory documents; no company email or system-access provisioning;
    no departmental-head steps; device allocation optional and a month out.
    Anything asserted absent here was deliberately REMOVED — reintroducing it
    is a policy change, not a refactor.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    owners = set(onboarding.items.values_list("owner", flat=True))
    kinds = set(onboarding.items.values_list("kind", flat=True))
    titles = set(onboarding.items.values_list("title", flat=True))

    assert ItemOwner.HR in owners
    assert ItemOwner.EMPLOYEE in owners
    # Admin, the department head AND the manager induction lines were all
    # removed from onboarding — the checklist is documents, the handbook and
    # HR's own steps, nothing routed to other people.
    assert ItemOwner.MANAGER not in owners
    assert ItemOwner.ADMIN not in owners
    assert ItemOwner.DEPARTMENT_HEAD not in owners

    # Email and system access are handled outside onboarding entirely.
    assert ItemKind.ACCOUNT not in kinds
    assert not any("email" in title.lower() for title in titles)
    assert not any("system access" in title.lower() for title in titles)

    # Every DOCUMENT line collects a MANDATORY document type.
    for item in onboarding.items.filter(kind=ItemKind.DOCUMENT):
        assert item.is_mandatory, item.title
        if item.document_type_id:
            assert item.document_type.is_mandatory, item.title

    # A device is not a day-one requirement: optional, due a month out.
    device = onboarding.items.get(kind=ItemKind.ASSET)
    assert not device.is_mandatory
    assert (device.due_date - employee.date_of_joining).days == 30


def test_the_new_hire_acknowledges_the_handbook_themselves(api, new_hire):
    """
    The acknowledgement is the EMPLOYEE's own act, post-first-login: /me says
    it is owed, the employee completes their own item through the ordinary
    endpoint (ONBOARDING EDIT at SELF), and /me stops asking. No HR involved.
    """
    api.force_authenticate(user=new_hire.user)

    me = api.get("/api/v1/me/")
    assert me.status_code == 200
    assert me.data["handbook_acknowledgement_pending"] is True

    onboarding = EmployeeOnboarding.objects.get(employee=new_hire.employee)
    item = onboarding.items.get(kind=ItemKind.ACKNOWLEDGEMENT)
    response = api.post(f"/api/v1/onboarding-items/{item.pk}/complete/", {})
    assert response.status_code == 200, response.data

    item.refresh_from_db()
    assert item.status == ItemStatus.COMPLETED
    assert item.completed_by_id == new_hire.user.pk

    me = api.get("/api/v1/me/")
    assert me.data["handbook_acknowledgement_pending"] is False


def test_the_subject_cannot_tick_other_peoples_items_or_waive_their_own(
    api, new_hire, staff
):
    """
    Self-certification, refused. The joiner's SELF-scoped EDIT exists so they
    can acknowledge the handbook — it must not let them mark the manager's
    induction done or waive a requirement off their own checklist.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=new_hire.employee)
    manager_task = _extra_task(
        onboarding,
        owner=ItemOwner.MANAGER,
        assigned_to=new_hire.employee.reporting_manager,
        title="Set expectations (custom template line)",
    )
    own_optional = onboarding.items.get(kind=ItemKind.ASSET)

    api.force_authenticate(user=new_hire.user)

    response = api.post(f"/api/v1/onboarding-items/{manager_task.pk}/complete/", {})
    assert response.status_code == 400
    assert "not by the joining employee" in str(response.data)

    response = api.post(
        f"/api/v1/onboarding-items/{own_optional.pk}/waive/",
        {"reason": "I do not think I need a device at all."},
    )
    assert response.status_code == 400
    assert "waived by HR" in str(response.data)

    manager_task.refresh_from_db()
    own_optional.refresh_from_db()
    assert manager_task.status == ItemStatus.PENDING
    assert own_optional.status == ItemStatus.PENDING

    # The people the items belong to are unaffected: the manager completes
    # their own line, and HR waives what HR judges waivable.
    api.force_authenticate(user=manager_task.assigned_to.user)
    response = api.post(f"/api/v1/onboarding-items/{manager_task.pk}/complete/", {})
    assert response.status_code == 200, response.data

    api.force_authenticate(user=staff["hr_head"].user)
    response = api.post(
        f"/api/v1/onboarding-items/{own_optional.pk}/waive/",
        {"reason": "Role needs no company device."},
    )
    assert response.status_code == 200, response.data


def test_owners_resolve_to_actual_people(employee, staff):
    """
    A MANAGER-owned line lands on the reporting manager, not in a void.

    The DEFAULT template no longer carries manager lines, so this pins the
    resolver directly — the mechanism a custom template's manager items
    ride on.
    """
    from apps.onboarding.services import _resolve_assignee

    assert _resolve_assignee(ItemOwner.MANAGER, employee) == staff["medical_director"]
    assert _resolve_assignee(ItemOwner.EMPLOYEE, employee) == employee
    assert _resolve_assignee(ItemOwner.HR, employee) is None  # a queue, not a person


def test_due_dates_are_relative_to_the_joining_date(employee):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    for item in onboarding.items.all():
        assert item.due_date is not None
        offset = (item.due_date - employee.date_of_joining).days
        assert offset == item.source_item.due_offset_days


def test_issuing_a_checklist_twice_returns_the_same_one(employee, staff):
    """Idempotent — a retry must not hand someone two checklists."""
    first = EmployeeOnboarding.objects.get(employee=employee)
    second = start_onboarding(employee=employee, actor=staff["hr_head"].user)
    assert second.pk == first.pk
    assert EmployeeOnboarding.objects.filter(employee=employee).count() == 1


def test_the_checklist_is_a_copy_not_a_reference(employee, lifecycle_config):
    """
    Editing the template must not rewrite history.

    An employee's checklist records what was asked of THEM; adding a step to
    the template a year later must not make a completed onboarding incomplete.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    before = onboarding.items.count()

    template = OnboardingTemplate.objects.get(is_default=True)
    OnboardingTemplateItem.objects.create(
        template=template, title="A step invented later", order=999, kind=ItemKind.TASK
    )

    assert onboarding.items.count() == before


# ===================================================== template selection


def test_a_department_template_beats_the_default(db, staff, org, lifecycle_config):
    from apps.employees.services.creation import create_employee
    from core.access.catalog import DepartmentKind, Layer

    medical = org["departments"][DepartmentKind.MEDICAL]
    specific = OnboardingTemplate.objects.create(
        name="Medical onboarding", department=medical
    )
    OnboardingTemplateItem.objects.create(
        template=specific, title="Clinical induction", order=10, kind=ItemKind.TASK
    )

    result = create_employee(
        actor=staff["hr_head"].user,
        first_name="Ravi", last_name="Kumar", email="ravi.k@lifecycle.test",
        role_code="therapist",
        department_id=medical.pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        reporting_manager_id=staff["medical_director"].pk,
        date_of_joining=dt.date(2026, 3, 1),
    )

    onboarding = EmployeeOnboarding.objects.get(employee=result.employee)
    assert onboarding.template_id == specific.pk
    assert onboarding.items.filter(title="Clinical induction").exists()


def test_the_default_applies_when_nothing_more_specific_fits(employee, lifecycle_config):
    template = resolve_template(employee)
    assert template is not None
    assert template.is_default is True


def test_a_hire_still_succeeds_with_no_template_configured(db, staff, org):
    """
    An organisation that has not set up onboarding must still be able to hire.

    A missing checklist is a configuration gap, not a reason to block a
    person's employment.
    """
    from apps.employees.services.creation import create_employee
    from core.access.catalog import DepartmentKind, Layer

    OnboardingTemplate.objects.all().delete()

    result = create_employee(
        actor=staff["hr_head"].user,
        first_name="Nina", last_name="Roy", email="nina.roy@lifecycle.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        reporting_manager_id=staff["medical_director"].pk,
        date_of_joining=dt.date(2026, 4, 1),
    )
    onboarding = EmployeeOnboarding.objects.get(employee=result.employee)
    assert onboarding.items.count() == 0
    assert onboarding.template is None


# ===================================================== working the checklist


def test_completing_a_task_records_who_and_when(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    item = _extra_task(onboarding)

    complete_item(item=item, actor=staff["hr_head"].user, notes="Done in the induction session.")
    item.refresh_from_db()

    assert item.status == ItemStatus.COMPLETED
    assert item.completed_by_id == staff["hr_head"].user.pk
    assert item.completed_at is not None
    assert item.notes == "Done in the induction session."


def test_a_document_item_cannot_be_ticked_off_on_someones_word(employee, staff):
    """
    The difference between a checklist that records reality and one that
    records optimism.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    item = onboarding.items.filter(kind=ItemKind.DOCUMENT).first()

    with pytest.raises(ValidationError) as exc:
        complete_item(item=item, actor=staff["hr_head"].user)
    assert "requires the uploaded file" in str(exc.value)

    item.refresh_from_db()
    assert item.status == ItemStatus.PENDING


def test_waiving_an_item_requires_a_reason_and_is_not_completion(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    item = _extra_task(onboarding)

    with pytest.raises(ValidationError):
        waive_item(item=item, actor=staff["hr_head"].user, reason="")

    waive_item(item=item, actor=staff["hr_head"].user, reason="Covered by the group induction.")
    item.refresh_from_db()

    # WAIVED, not COMPLETED — a waived mandatory item must never read as done.
    assert item.status == ItemStatus.WAIVED
    assert item.is_done is True


def test_an_item_cannot_be_completed_twice(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    item = _extra_task(onboarding)

    complete_item(item=item, actor=staff["hr_head"].user)
    with pytest.raises(ValidationError):
        complete_item(item=item, actor=staff["hr_head"].user)


def test_reopening_an_item_reopens_the_checklist(employee, staff):
    """
    A completed checklist with an outstanding item is the kind of quiet
    inconsistency nobody notices until an audit.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    actor = staff["hr_head"].user

    for item in onboarding.items.filter(is_mandatory=True):
        waive_item(item=item, actor=actor, reason="Handled outside the system for this test.")

    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.COMPLETED

    reopen_item(item=onboarding.items.first(), actor=actor, reason="Document was rejected.")
    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.IN_PROGRESS


# ===================================================== completion


def test_the_checklist_closes_when_the_mandatory_items_are_done(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    actor = staff["hr_head"].user

    for item in onboarding.items.filter(is_mandatory=True):
        waive_item(item=item, actor=actor, reason="Satisfied through the group induction session.")

    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.COMPLETED
    assert onboarding.completed_at is not None


def test_optional_items_do_not_hold_the_checklist_open(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    actor = staff["hr_head"].user

    for item in onboarding.items.filter(is_mandatory=True):
        waive_item(item=item, actor=actor, reason="Satisfied through the group induction session.")

    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.COMPLETED
    # An optional item is still open, and that is fine.
    assert onboarding.items.filter(is_mandatory=False, status=ItemStatus.PENDING).exists()


def test_closing_by_hand_is_refused_while_mandatory_items_are_outstanding(employee, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=employee)

    with pytest.raises(ValidationError) as exc:
        complete_onboarding(onboarding=onboarding, actor=staff["hr_head"].user)
    assert "mandatory item" in str(exc.value)

    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.IN_PROGRESS


def test_forcing_a_close_takes_a_heavier_permission(employee, staff):
    """
    `force` demands ONBOARDING/APPROVE, which HR Manager does not hold — so
    closing over an outstanding requirement is not the same authority as
    ticking one off.
    """
    onboarding = EmployeeOnboarding.objects.get(employee=employee)

    with pytest.raises(AccessDenied):
        complete_onboarding(
            onboarding=onboarding, actor=staff["hr_manager"].user, force=True
        )

    complete_onboarding(onboarding=onboarding, actor=staff["hr_head"].user, force=True)
    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.COMPLETED


def test_completion_is_audited(employee, staff):
    from apps.audit.models import AuditLog

    onboarding = EmployeeOnboarding.objects.get(employee=employee)
    complete_onboarding(onboarding=onboarding, actor=staff["hr_head"].user, force=True)

    # Filtered to the SEMANTIC event: the signal layer also logs the row's
    # field-level UPDATE, and that entry is newer, so "the latest row" is not
    # the one under test.
    entry = AuditLog.objects.filter(
        entity_type="onboarding.EmployeeOnboarding",
        entity_id=str(onboarding.pk),
        after__event="onboarding_completed",
    ).first()
    assert entry is not None
    assert entry.actor_id == staff["hr_head"].user.pk


# ===================================================== configuration, not code


def test_a_template_of_entirely_invented_steps_is_honoured(db, staff, org, lifecycle_config):
    """
    THE ANTI-HARD-CODING TEST.

    None of these steps exist anywhere in this codebase. A hire against this
    template must receive exactly them, in order, with their owners and due
    offsets — which is only possible if the checklist is genuinely read from
    configuration.
    """
    from apps.employees.services.creation import create_employee
    from core.access.catalog import DepartmentKind, Layer

    operations = org["departments"][DepartmentKind.OPERATIONS]
    template = OnboardingTemplate.objects.create(
        name="Guild induction", department=operations
    )
    invented = [
        ("Issue guild insignia", ItemKind.ASSET, ItemOwner.ADMIN, 0, 10),
        ("Swear the guild oath", ItemKind.ACKNOWLEDGEMENT, ItemOwner.DEPARTMENT_HEAD, 1, 20),
        ("Assign a workshop bench", ItemKind.TASK, ItemOwner.MANAGER, 3, 30),
    ]
    for title, kind, owner, offset, order in invented:
        OnboardingTemplateItem.objects.create(
            template=template, title=title, kind=kind, owner=owner,
            due_offset_days=offset, order=order,
        )

    result = create_employee(
        actor=staff["hr_head"].user,
        first_name="Ines", last_name="Rocha", email="ines.rocha@lifecycle.test",
        role_code="office_boy",
        department_id=operations.pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        reporting_manager_id=staff["operational_head"].pk,
        date_of_joining=dt.date(2026, 5, 4),
    )

    onboarding = EmployeeOnboarding.objects.get(employee=result.employee)
    titles = list(onboarding.items.order_by("order").values_list("title", flat=True))
    assert titles == [title for title, *_ in invented]

    oath = onboarding.items.get(title="Swear the guild oath")
    assert oath.owner == ItemOwner.DEPARTMENT_HEAD
    assert oath.due_date == dt.date(2026, 5, 5)
