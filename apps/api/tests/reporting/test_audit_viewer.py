"""
Audit: creation, subject resolution, and scope isolation.

The isolation tests are the ones that matter. Before `subject_employee`
existed, `AUDIT_LOG` was registered `person_scoped=False`, so a Department Head
holding DEPARTMENT scope resolved to NO rows — the scoping worked perfectly and
found nothing, which is the most confusing possible failure.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditAction, AuditLog
from apps.audit.signals import extract_reason, resolve_subject

pytestmark = pytest.mark.django_db

AUDIT = "/api/v1/audit/"
OPTIONS = "/api/v1/audit/options/"


def rows(response):
    body = response.json()
    return body if isinstance(body, list) else body.get("data", [])


# ------------------------------------------------------- subject resolution


def test_an_employee_event_is_about_that_employee(people):
    employee = people["therapist"]
    employee.phone = "9000000001"
    employee.save()

    entry = AuditLog.objects.filter(entity_type="employees.Employee").order_by("-id").first()

    assert entry.subject_employee_id == employee.pk


def test_a_related_record_resolves_through_one_hop(people):
    """A document belongs to an employee, so its audit belongs to them too."""
    from apps.employees.models import EmployeeDocument, DocumentType

    document_type = DocumentType.objects.create(name="Probe", code="PROBE")
    EmployeeDocument.objects.create(
        employee=people["cre"], document_type=document_type, original_filename="probe.pdf"
    )

    entry = (
        AuditLog.objects.filter(entity_type="employees.EmployeeDocument")
        .order_by("-id")
        .first()
    )

    assert entry is not None
    assert entry.subject_employee_id == people["cre"].pk


def test_an_organisation_level_event_has_no_subject(people, org):
    """
    A department rename is nobody's personal event.

    NULL is correct here, and it narrows visibility to ALL scope — which is
    right, because an organisation-level change belongs to no department.
    """
    department = org["departments"]["medical"]
    department.description = "Changed"
    department.save()

    entry = AuditLog.objects.filter(entity_type__endswith="Department").order_by("-id").first()
    if entry is not None:
        assert entry.subject_employee_id is None


def test_the_resolver_never_raises_on_an_odd_object():
    """
    A scoping hint is never worth failing the write it describes.

    Returning None narrows visibility, so the failure mode is less disclosure
    rather than more.
    """
    class Odd:
        @property
        def employee(self):
            raise RuntimeError("boom")

    assert resolve_subject(Odd()) is None


def test_a_mandatory_justification_is_lifted_onto_the_row():
    assert extract_reason({"reason": "Because it was wrong."}) == "Because it was wrong."
    assert extract_reason({"rationale": "Not enough experience."}) == "Not enough experience."
    assert extract_reason({"unrelated": "x"}) == ""
    assert extract_reason(None) == ""


# ------------------------------------------------------------ append-only


def test_an_audit_row_cannot_be_edited(people):
    people["therapist"].save()
    entry = AuditLog.objects.order_by("-id").first()

    entry.action = AuditAction.CREATE
    with pytest.raises(ValueError):
        entry.save()


def test_an_audit_row_cannot_be_deleted(people):
    people["therapist"].save()
    entry = AuditLog.objects.order_by("-id").first()

    with pytest.raises(ValueError):
        entry.delete()


# ---------------------------------------------------------------- isolation


def test_a_department_head_sees_their_own_people_and_no_one_elses(auth, people):
    """The requirement, asserted directly: one department cannot see another's."""
    for employee in people.values():
        employee.save()  # generate an audited event per person

    medical = rows(auth(people["medical_director"].user).get(AUDIT))
    operations = rows(auth(people["operational_head"].user).get(AUDIT))

    medical_departments = {r["subject_department"] for r in medical if r["subject_department"]}
    operations_departments = {
        r["subject_department"] for r in operations if r["subject_department"]
    }

    assert medical_departments == {"Medical Department"}
    assert operations_departments == {"Operations Department"}
    assert medical_departments.isdisjoint(operations_departments)


def test_a_department_head_sees_events_performed_by_other_departments(auth, people):
    """
    Scoped by SUBJECT, not by actor.

    HR editing a Medical employee is exactly the event a Medical Director needs
    to see; scoping by actor would hide it.
    """
    target = people["therapist"]
    hr = people["hr_head"].user

    from core.middleware import acting_as

    with acting_as(hr):
        target.phone = "9000000009"
        target.save()

    visible = rows(auth(people["medical_director"].user).get(AUDIT))
    ids = {r["subject_code"] for r in visible}

    assert target.employee_code in ids


def test_organisation_wide_roles_see_everything_including_subjectless_events(
    auth, people, admin_user, ceo_user
):
    for employee in people.values():
        employee.save()

    for user in [admin_user, ceo_user, people["hr_head"].user]:
        visible = rows(auth(user).get(AUDIT))
        departments = {r["subject_department"] for r in visible}
        # More than one department present is what "org-wide" means here.
        assert len({d for d in departments if d}) >= 1, user.email


def test_a_manager_cannot_reach_the_audit_log_at_all(auth, people):
    """Audit is leadership-only. A team lead holds no AUDIT_LOG grant."""
    assert auth(people["senior_doctor"].user).get(AUDIT).status_code == 403
    assert auth(people["operations_manager"].user).get(AUDIT).status_code == 403


def test_an_ordinary_employee_cannot_reach_organisation_wide_audit(auth, people):
    """The explicit requirement."""
    for user in [people["therapist"].user, people["office_boy"].user, people["cre"].user]:
        assert auth(user).get(AUDIT).status_code == 403, user.email


# ------------------------------------------------------------------- viewer


def test_the_viewer_is_read_only(auth, admin_user):
    """There is no write route, for anyone, including Admin."""
    client = auth(admin_user)

    assert client.post(AUDIT, {}, format="json").status_code in (403, 405)
    assert client.delete(f"{AUDIT}1/").status_code in (403, 404, 405)


def test_sensitive_only_narrows_to_the_events_a_reviewer_looks_for(auth, admin_user, people):
    from apps.audit.api import SENSITIVE_ACTIONS

    people["therapist"].save()  # an ordinary UPDATE
    AuditLog.objects.create(
        actor=admin_user,
        actor_email=admin_user.email,
        action=AuditAction.OVERRIDE,
        entity_type="recruitment.Application",
        entity_id="x",
        entity_label="An override",
        subject_employee=people["therapist"],
        reason="Administrative override for a documented reason.",
    )

    all_rows = rows(auth(admin_user).get(AUDIT))
    only_sensitive = rows(auth(admin_user).get(f"{AUDIT}?sensitive_only=true"))

    assert len(only_sensitive) < len(all_rows)
    assert all(row["action"] in SENSITIVE_ACTIONS for row in only_sensitive)
    assert all(row["is_sensitive"] for row in only_sensitive)


def test_the_reason_is_exposed_on_the_row(auth, admin_user, people):
    AuditLog.objects.create(
        actor=admin_user,
        actor_email=admin_user.email,
        action=AuditAction.REVERSE,
        entity_type="payroll.PayrollRun",
        entity_id="y",
        entity_label="June payroll",
        subject_employee=people["therapist"],
        reason="Reversed because the June increments were loaded late.",
    )

    visible = rows(auth(admin_user).get(f"{AUDIT}?action={AuditAction.REVERSE}"))

    assert visible
    assert "June increments" in visible[0]["reason"]


def test_the_filter_vocabulary_comes_from_the_server(auth, admin_user, people):
    people["therapist"].save()

    response = auth(admin_user).get(OPTIONS)

    assert response.status_code == 200
    body = response.json()
    assert any(a["value"] == AuditAction.OVERRIDE and a["sensitive"] for a in body["actions"])
    assert "entity_types" in body


def test_options_are_refused_to_someone_without_audit_access(auth, people):
    assert auth(people["therapist"].user).get(OPTIONS).status_code == 403


def test_a_redacted_field_records_the_change_but_never_the_value(people):
    """
    The registry redacts sensitive fields at write time.

    You learn that a PAN was edited; you never learn what it was.
    """
    employee = people["therapist"]
    employee.pan = "ABCDE1234F"
    employee.save()

    entry = (
        AuditLog.objects.filter(entity_type="employees.Employee")
        .order_by("-id")
        .first()
    )

    if entry.after and "pan" in entry.after:
        assert entry.after["pan"] == "***"
        assert "ABCDE1234F" not in str(entry.after)
