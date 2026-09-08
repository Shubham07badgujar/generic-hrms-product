"""
Notifications: creation, visibility, read state and the polling contract.

The load-bearing assertion is that notifications are addressed to a USER, not
an Employee. Admin and CEO hold no Employee record; any employee-based scoping
would silently hide every notification from exactly the two principals who most
need them, and the failure would look like "no notifications yet".
"""

from __future__ import annotations

import pytest

from apps.notifications import events, services
from apps.notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationKind,
    NotificationPreference,
    Priority,
)

pytestmark = pytest.mark.django_db

LIST = "/api/v1/notifications/"
UNREAD = "/api/v1/notifications/unread-count/"
READ_ALL = "/api/v1/notifications/read-all/"
PREFS = "/api/v1/notifications/preferences/"


def make(user, **kwargs):
    defaults = {
        "kind": NotificationKind.PAYROLL_APPROVED,
        "title": "Something happened",
    }
    return services.notify(recipient=user, **{**defaults, **kwargs})


# ------------------------------------------------------------------ creation


def test_notify_creates_a_row_and_records_its_deliveries(people):
    user = people["therapist"].user
    notification = make(user, body="Details here.", link_url="/payroll/1")

    assert notification is not None
    assert notification.recipient == user
    # In-app is a no-op transport on top of the row, but it is still recorded,
    # so reporting does not have to treat it as an implicit special case.
    channels = {d.channel: d.status for d in notification.deliveries.all()}
    assert channels["in_app"] == DeliveryStatus.SENT
    assert channels["email"] == DeliveryStatus.SUPPRESSED


def test_notifying_an_inactive_user_creates_nothing(people):
    user = people["cre"].user
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert make(user) is None


def test_a_repeated_pending_fact_does_not_become_a_second_row(people):
    """
    A probation review still due tomorrow is not new information.

    Without dedupe the nightly sweep would add a row a day until someone acted,
    which is how people learn to ignore the notification that matters.
    """
    user = people["hr_head"].user

    first = make(user, dedupe_key="probation:1:due")
    second = make(user, dedupe_key="probation:1:due")

    assert first is not None
    assert second is None
    assert Notification.objects.filter(recipient=user, dedupe_key="probation:1:due").count() == 1


def test_dedupe_only_suppresses_while_the_first_is_unread(people):
    """Once acted on, the same fact recurring IS new information."""
    user = people["hr_head"].user
    first = make(user, dedupe_key="clearance:9")
    services.mark_read(first, user=user)

    second = make(user, dedupe_key="clearance:9")

    assert second is not None


def test_a_failure_inside_notify_never_reaches_the_caller(people, monkeypatch):
    """
    Notifying must never break the thing it is notifying about.

    A payroll approval that failed because the notification table was busy
    would be a far worse defect than a missing notification.
    """
    monkeypatch.setattr(
        services.Notification.objects, "create",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db is on fire")),
    )

    assert make(people["therapist"].user) is None


# ---------------------------------------------------------------- preferences


def test_a_kind_switched_off_creates_nothing(people):
    user = people["therapist"].user
    services.set_preference(
        user=user, kind=NotificationKind.PAYROLL_APPROVED, in_app=False, email=False
    )

    assert make(user, kind=NotificationKind.PAYROLL_APPROVED) is None


def test_an_unconfigured_kind_defaults_to_on(people):
    """Defaulting to silence would make the system depend on finding a settings screen."""
    user = people["cre"].user
    assert not NotificationPreference.objects.filter(user=user).exists()

    assert make(user) is not None


def test_critical_ignores_preference(people):
    """
    A blocked payroll run is not a matter of taste.

    Letting CRITICAL be switched off turns a preference into a way of missing
    an obligation someone else is waiting on.
    """
    user = people["therapist"].user
    services.set_preference(
        user=user, kind=NotificationKind.CLEARANCE_TASK_ASSIGNED, in_app=False, email=False
    )

    result = make(
        user, kind=NotificationKind.CLEARANCE_TASK_ASSIGNED, priority=Priority.CRITICAL
    )

    assert result is not None


def test_an_unknown_kind_is_refused(people):
    with pytest.raises(ValueError):
        services.set_preference(
            user=people["cre"].user, kind="not_a_real_kind", in_app=True, email=False
        )


# ------------------------------------------------------------------ visibility


def test_a_notification_is_visible_only_to_its_recipient(people):
    mine = make(people["therapist"].user, title="Mine")
    make(people["cre"].user, title="Theirs")

    visible = services.visible_to(people["therapist"].user)

    assert list(visible) == [mine]


def test_admin_and_ceo_receive_notifications_despite_having_no_employee_record(
    admin_user, ceo_user
):
    """
    The reason visibility is by User rather than Employee.

    Both of these principals are system-level and hold no Employee row, so any
    employee-path scoping would return nothing and look like "none yet".
    """
    assert admin_user.employee is None if hasattr(admin_user, "employee") else True

    assert make(admin_user, title="For admin") is not None
    assert make(ceo_user, title="For the CEO") is not None
    assert services.visible_to(admin_user).count() == 1
    assert services.visible_to(ceo_user).count() == 1


# ------------------------------------------------------------------ read state


def test_marking_read_sets_the_timestamp_and_drops_the_count(people):
    user = people["therapist"].user
    notification = make(user)
    assert services.unread_count(user) == 1

    services.mark_read(notification, user=user)
    notification.refresh_from_db()

    assert notification.is_read is True
    assert notification.read_at is not None
    assert services.unread_count(user) == 0


def test_one_person_cannot_mark_anothers_notification_read(people):
    theirs = make(people["cre"].user)

    with pytest.raises(PermissionError):
        services.mark_read(theirs, user=people["therapist"].user)


def test_mark_all_read_touches_only_the_callers_own(people):
    mine = [make(people["therapist"].user, title=f"m{i}", dedupe_key=f"m{i}") for i in range(3)]
    theirs = make(people["cre"].user, title="theirs")

    marked = services.mark_all_read(people["therapist"].user)
    theirs.refresh_from_db()

    assert marked == 3
    assert theirs.is_read is False


# ------------------------------------------------------------------- the API


def test_the_list_endpoint_returns_only_the_callers_own(auth, people):
    make(people["therapist"].user, title="Mine")
    make(people["cre"].user, title="Theirs")

    response = auth(people["therapist"].user).get(LIST)

    assert response.status_code == 200
    titles = [row["title"] for row in response.json()["data"]]
    assert titles == ["Mine"]


def test_the_polling_endpoint_returns_a_count(auth, people):
    make(people["therapist"].user, dedupe_key="a")
    make(people["therapist"].user, dedupe_key="b")

    response = auth(people["therapist"].user).get(UNREAD)

    assert response.status_code == 200
    assert response.json() == {"unread": 2}


def test_every_role_can_reach_their_own_notifications(auth, people, admin_user, ceo_user):
    """
    Including the CEO.

    Before NOTIFICATION was added to their readable set the CEO got a 403 here
    — not an empty list, a hard refusal — which is worse than useless.
    """
    for user in [people["therapist"].user, people["hr_head"].user, admin_user, ceo_user]:
        assert auth(user).get(LIST).status_code == 200, user.email
        assert auth(user).get(UNREAD).status_code == 200, user.email


def test_a_read_only_principal_cannot_mark_notifications_read(auth, ceo_user):
    """
    A real limitation, kept deliberately.

    The CEO's list is read-only like everything else they touch. Exempting this
    route would put the first hole in a control that is currently absolute.
    """
    make(ceo_user, title="For the CEO")

    assert auth(ceo_user).post(READ_ALL, {}, format="json").status_code == 403


def test_preferences_list_every_kind_including_unconfigured_ones(auth, people):
    response = auth(people["therapist"].user).get(PREFS)

    assert response.status_code == 200
    rows = response.json()["preferences"]
    assert len(rows) == len(NotificationKind.choices)
    assert all(row["configured"] is False for row in rows)


def test_saving_a_preference_marks_it_configured(auth, people):
    client = auth(people["therapist"].user)
    client.post(
        PREFS,
        {"kind": NotificationKind.PAYSLIP_AVAILABLE, "in_app": False, "email": True},
        format="json",
    )

    rows = {row["kind"]: row for row in client.get(PREFS).json()["preferences"]}
    row = rows[NotificationKind.PAYSLIP_AVAILABLE]

    assert row["configured"] is True
    assert row["in_app"] is False
    assert row["email"] is True


# ------------------------------------------------------- workflow integration


def test_recipients_come_from_the_permission_matrix_not_a_role_list(people):
    """
    Granting a permission should also start telling that role about the work.

    A hardcoded role list is how a queue ends up silently unwatched after a
    permission change.
    """
    from core.access import Action, Resource

    recipients = events._users_holding(Resource.PAYROLL_RUN, Action.APPROVE)
    emails = {user.email for user in recipients}

    assert people["finance_head"].user.email in emails
    assert people["therapist"].user.email not in emails


def test_probation_reminder_notifies_hr_and_the_manager(people):
    """The system never auto-confirms, so somebody must be told to decide."""
    from apps.employees.models import ProbationReview

    employee = people["therapist"]
    review = ProbationReview.objects.create(
        employee=employee,
        probation_end_date=employee.date_of_joining,
        reviewer=employee.reporting_manager,
    )

    events.probation_review_due(review)

    hr_head = people["hr_head"].user
    manager = people["senior_doctor"].user
    assert Notification.objects.filter(
        recipient=hr_head, kind=NotificationKind.PROBATION_REVIEW_DUE
    ).exists()
    assert Notification.objects.filter(
        recipient=manager, kind=NotificationKind.PROBATION_REVIEW_DUE
    ).exists()
