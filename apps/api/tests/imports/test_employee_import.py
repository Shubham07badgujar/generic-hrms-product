"""
Importing a staff list: the four things that make it different from one hire.

Bulk creation is not "the single path in a loop", and each way it differs is a
failure that only appears at scale:

  * seats are decided for the WHOLE batch before anything is written, so a file
    larger than the plan allows imports nobody rather than the first N;
  * the employee-code counter is locked once, so 200 hires do not take 200
    lock-and-release cycles and the codes come out consecutive;
  * nobody is emailed from inside the transaction, because 200 SMTP
    conversations bounded by EMAIL_TIMEOUT is up to fifty minutes of blocked
    worker while the import still holds its locks;
  * a preview writes nothing at all, so a file can be fixed and re-uploaded.

The two-phase rule the candidate importer follows holds here too: the commit
re-resolves every reference against the database rather than replaying what the
preview decided.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command

from apps.imports.models import BatchStatus, ImportKind, ImportRow, RowStatus
from apps.imports.services.employee_import import (
    commit_employee_batch,
    create_employee_batch,
)
from tests.conftest import across_organizations
from tests.imports.conftest import EMPLOYEE_HEADERS

# The two-organization world, for the one test that asks whether a name can
# reach across a tenant boundary.
from tests.tenancy.conftest import org_b  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def hr(staff):
    """The HR Head: holds EMPLOYEE/IMPORT, and hires into every department."""
    return staff["hr_head"].user


@pytest.fixture
def structure(db, organization):
    """A department and a location for the sheet to name."""
    from apps.organization.models import Department, Location
    from core.access.catalog import DepartmentKind

    department, _ = Department.objects.get_or_create(
        code="PEOPLE", defaults={"name": "People", "kind": DepartmentKind.HR}
    )
    location, _ = Location.objects.get_or_create(
        code="HO", defaults={"name": "Head Office", "city": "Pune", "state": "MH"}
    )
    return {"department": department, "location": location}


def _row(name, email, *, department="People", joining="2024-01-15", manager="", **over):
    """
    One sheet row.

    `manager` defaults to empty and the tests pass one, because the product
    requires it: anyone below department head must report to somebody, and an
    import does not get to bypass a rule the hire form enforces.
    """
    values = {
        "Employee Code": "",
        "Name": name,
        "Work Email": email,
        "Personal Email": f"{email.split('@')[0]}.personal@example.test",
        "Mobile": "9876543210",
        "Department": department,
        "Designation": "",
        "Location": "",
        "Level": "",
        "Reporting Manager": manager,
        "Role": "",
        "Employment Type": "",
        "Date of Joining": joining,
    }
    values.update(over)
    return [values[header] for header in EMPLOYEE_HEADERS]


@pytest.fixture
def manager_code(staff):
    """An existing employee for imported staff to report to, by employee code."""
    return staff["hr_head"].employee_code


@pytest.fixture
def two_staff(manager_code):
    """The ordinary case: two people, each reporting to somebody who exists."""
    return [
        _row("Asha Rao", "asha@acme.test", manager=manager_code),
        _row("Vikram Bose", "vikram@acme.test", manager=manager_code),
    ]


# ------------------------------------------------------------------ preview


def test_a_preview_stages_rows_and_hires_nobody(
    hr, structure, employee_xlsx, two_staff
):
    """
    Phase one writes staging rows and nothing else.

    The whole point of two phases: a 200-row sheet with three bad cells can be
    fixed and re-uploaded, and until somebody commits it there are no logins,
    no onboarding checklists and no emails to undo.
    """
    from apps.accounts.models import User
    from apps.employees.models import Employee

    before = Employee.objects.count()

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))

    assert batch.kind == ImportKind.EMPLOYEES
    assert batch.job_opening_id is None, "a staff list has no job to apply to"
    assert batch.status == BatchStatus.PARSED
    assert batch.rows_total == 2
    assert ImportRow.objects.filter(batch=batch, status=RowStatus.VALID).count() == 2

    assert Employee.objects.count() == before
    assert not User.objects.filter(email="asha@acme.test").exists()


def test_a_row_missing_what_a_hire_needs_is_refused_before_commit(
    hr, structure, employee_xlsx
):
    """Reported per row, with a code, so a sheet can be fixed in one pass."""
    rows = [
        _row("", "noname@acme.test"),
        _row("No Email", ""),
        _row("No Department", "nodept@acme.test", department=""),
        _row("Bad Date", "baddate@acme.test", joining="not a date"),
    ]
    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=rows))

    codes = {
        row.row_number: {error["code"] for error in row.errors}
        for row in ImportRow.objects.filter(batch=batch).order_by("row_number")
    }
    assert "missing_name" in codes[2]
    assert "missing_email" in codes[3]
    assert "missing_department" in codes[4]
    assert "missing_or_unreadable_date" in codes[5]
    # Every one of these rows also names nobody to report to, which the
    # hierarchy rule refuses for anyone below department head. Caught at
    # preview so a 200-row sheet says so once instead of failing 200 times.
    assert all("missing_reporting_manager" in codes[n] for n in codes)
    assert not ImportRow.objects.filter(batch=batch, status=RowStatus.VALID).exists()


def test_an_address_that_already_has_a_login_is_caught_at_preview(
    hr, structure, employee_xlsx, manager_code
):
    """
    Email is the username and is unique platform-wide, so this is the V1
    identity clamp arriving where an operator can act on it: fix the sheet,
    rather than watch row 14 fail at commit.
    """
    taken = _row("Already Here", hr.email, manager=manager_code)
    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=[taken]))

    row = ImportRow.objects.get(batch=batch, row_number=2)
    assert {e["code"] for e in row.errors} == {"email_already_has_login"}


def test_the_same_address_twice_in_one_file_is_a_duplicate(
    hr, structure, employee_xlsx, manager_code
):
    rows = [
        _row("First Copy", "twice@acme.test", manager=manager_code),
        _row("Second Copy", "twice@acme.test", manager=manager_code),
    ]
    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=rows))

    second = ImportRow.objects.get(batch=batch, row_number=3)
    assert second.status == RowStatus.DUPLICATE_IN_FILE
    assert second.duplicate_of_row == 2


def test_an_ambiguous_date_is_read_day_first_and_says_so(
    hr, structure, employee_xlsx, manager_code
):
    """
    `03/04/2025` is two different days depending on who typed it, and a joining
    date sets probation, accrual and the first payroll period. Day-first is the
    house convention; the warning is what stops it being silent.
    """
    batch = create_employee_batch(
        actor=hr,
        file=employee_xlsx(
            rows=[
                _row(
                    "Ambiguous Date", "amb@acme.test",
                    joining="03/04/2025", manager=manager_code,
                )
            ]
        ),
    )

    row = ImportRow.objects.get(batch=batch, row_number=2)
    assert row.status == RowStatus.VALID
    assert {w["code"] for w in row.warnings} == {"ambiguous_date_read_day_first"}

    # And an unambiguous one carries no warning at all.
    clear = create_employee_batch(
        actor=hr,
        file=employee_xlsx(
            rows=[
                _row(
                    "Clear Date", "clear@acme.test",
                    joining="25/12/2024", manager=manager_code,
                )
            ]
        ),
    )
    assert ImportRow.objects.get(batch=clear, row_number=2).warnings == []


# ------------------------------------------------------------------- commit


def test_committing_hires_everyone_with_logins_and_consecutive_codes(
    hr, structure, employee_xlsx, two_staff, django_capture_on_commit_callbacks
):
    from apps.accounts.models import User
    from apps.employees.models import Employee

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))
    with django_capture_on_commit_callbacks(execute=False):
        result = commit_employee_batch(actor=hr, batch=batch)

    assert result.created == 2 and result.failed == 0
    batch.refresh_from_db()
    assert batch.status == BatchStatus.COMPLETED

    asha = Employee.objects.get(user__email="asha@acme.test")
    vikram = Employee.objects.get(user__email="vikram@acme.test")
    assert asha.department_id == structure["department"].pk
    assert asha.date_of_joining == dt.date(2024, 1, 15)
    assert User.objects.get(email="asha@acme.test").must_change_password

    # One counter, locked once, handing out a consecutive block.
    codes = sorted([asha.employee_code, vikram.employee_code])
    assert codes[0] != codes[1]
    assert int(codes[1][-6:]) - int(codes[0][-6:]) == 1

    rows = ImportRow.objects.filter(batch=batch).order_by("row_number")
    assert [row.status for row in rows] == [RowStatus.CREATED, RowStatus.CREATED]
    assert all(row.created_employee_id for row in rows)


def test_nobody_is_emailed_from_inside_the_commit(
    hr, structure, employee_xlsx, two_staff, django_capture_on_commit_callbacks
):
    """
    THE SCALE TRAP. `create_employee` schedules one welcome email per hire,
    each opening its own SMTP conversation bounded by EMAIL_TIMEOUT. Two
    hundred of those is up to fifty minutes of blocked worker, every second of
    it while the import still holds the employee-code lock.

    So the hires are made with `send_welcome_email=False` and ONE task is
    queued after commit. Asserted by counting the callbacks: two hires, one
    callback.
    """
    from django.core import mail

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        commit_employee_batch(actor=hr, batch=batch)

    assert mail.outbox == [], "the commit itself sent mail"
    assert len(callbacks) == 1, (
        f"expected one batched send, got {len(callbacks)} scheduled callbacks"
    )


def test_the_batched_task_sends_one_message_per_person_over_one_connection(
    hr, structure, employee_xlsx, two_staff,
    django_capture_on_commit_callbacks, monkeypatch,
):
    """
    And the credentials are made in the task, not carried to it.

    A temporary password is stored nowhere, so passing two hundred of them as
    task arguments would write live credentials into the broker. The task
    generates each at send time -- which also means the address in the mail is
    usable, because the password it names is the one now on the account.
    """
    from django.core import mail

    from core.config import email_config as real_email_config

    opened = []

    class CountingConfig:
        """
        The resolved mail config, counting how often a connection is opened.

        A wrapper rather than a patched attribute: the config is a frozen
        dataclass, which is the right shape for it and not something to work
        around in a test.
        """

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def connection(self, *args, **kwargs):
            opened.append(1)
            return self._inner.connection(*args, **kwargs)

    monkeypatch.setattr(
        "core.config.email_config",
        lambda organization: CountingConfig(real_email_config(organization)),
    )

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        commit_employee_batch(actor=hr, batch=batch)

    assert len(mail.outbox) == 2, "one message per person hired"
    # Credentials go to the PERSONAL address: a new joiner cannot read a
    # company mailbox they have not signed in to create yet.
    assert {m.to[0] for m in mail.outbox} == {
        "asha.personal@example.test",
        "vikram.personal@example.test",
    }
    assert sum(opened) <= 1, (
        f"the batch opened {sum(opened)} SMTP connections; the point of "
        f"batching is that it opens one"
    )


def test_committing_twice_is_idempotent(
    hr, structure, employee_xlsx, two_staff, django_capture_on_commit_callbacks
):
    """A retried request reports what happened; it does not hire everyone again."""
    from apps.employees.models import Employee

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))
    with django_capture_on_commit_callbacks(execute=False):
        first = commit_employee_batch(actor=hr, batch=batch)
    after_first = Employee.objects.count()

    batch.refresh_from_db()
    second = commit_employee_batch(actor=hr, batch=batch)

    assert second.created == first.created
    assert Employee.objects.count() == after_first


def test_one_bad_row_does_not_cost_the_others_their_hire(
    hr, structure, employee_xlsx, manager_code, django_capture_on_commit_callbacks
):
    """
    A department nobody can find is a row failure, not a batch failure.

    The savepoint per row is what makes that true: without it a 200-row import
    would be decided by its worst line. The bad row is marked with a code the
    operator can act on.
    """
    from apps.employees.models import Employee

    rows = [
        _row("Good One", "good@acme.test", manager=manager_code),
        # Valid at preview -- a department name is only resolved against the
        # database at commit, which is the two-phase rule working.
        _row(
            "Bad Dept", "baddept@acme.test",
            department="Department Of Nowhere", manager=manager_code,
        ),
    ]
    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=rows))
    with django_capture_on_commit_callbacks(execute=False):
        result = commit_employee_batch(actor=hr, batch=batch)

    assert result.created == 1 and result.failed == 1
    batch.refresh_from_db()
    assert batch.status == BatchStatus.PARTIAL

    assert Employee.objects.filter(user__email="good@acme.test").exists()
    assert not Employee.objects.filter(user__email="baddept@acme.test").exists()

    failed = ImportRow.objects.get(batch=batch, row_number=3)
    assert failed.status == RowStatus.FAILED
    assert {e["code"] for e in failed.errors} == {"unknown_department"}


# -------------------------------------------------------------------- seats


def test_a_batch_larger_than_the_plan_allows_hires_nobody(
    hr, structure, employee_xlsx, two_staff, organization
):
    """
    THE WHOLE-BATCH RULE. `create_employee` reserves one seat at a time, which
    would import until the limit and then fail row by row -- leaving a
    half-migrated staff list somebody has to reconcile by hand.

    A refused import is one message. A partial one is a data-cleanup project.
    """
    from apps.employees.models import Employee
    from apps.platform.models import Plan, Subscription
    from apps.platform.services.subscriptions import SeatLimitReached

    plan = Plan.objects.create(
        code="tiny", name="Tiny", employee_limit=Employee.objects.count() + 1
    )
    # A subscription is the PLATFORM's row about a customer, so the fixture
    # writes it the way billing does.
    with across_organizations():
        Subscription.objects.create(organization=organization, plan=plan)

    before = Employee.objects.count()
    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))

    with pytest.raises(SeatLimitReached):
        commit_employee_batch(actor=hr, batch=batch)

    assert Employee.objects.count() == before, "a refused batch hired somebody"
    batch.refresh_from_db()
    assert batch.status == BatchStatus.PARSED, (
        "a refused batch must stay committable once seats are available"
    )


def test_a_batch_within_the_limit_still_commits(
    hr, structure, employee_xlsx, two_staff, organization,
    django_capture_on_commit_callbacks,
):
    """The positive control: the seat check refuses too much, not everything."""
    from apps.employees.models import Employee
    from apps.platform.models import Plan, Subscription

    plan = Plan.objects.create(
        code="roomy", name="Roomy", employee_limit=Employee.objects.count() + 50
    )
    # A subscription is the PLATFORM's row about a customer, so the fixture
    # writes it the way billing does.
    with across_organizations():
        Subscription.objects.create(organization=organization, plan=plan)

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))
    with django_capture_on_commit_callbacks(execute=False):
        result = commit_employee_batch(actor=hr, batch=batch)

    assert result.created == 2


# ----------------------------------------------------------------- tenancy


def test_a_department_in_another_organization_does_not_resolve(
    hr, structure, employee_xlsx, manager_code, org_b  # noqa: F811
):
    """
    Names are resolved through the tenant manager, so a department that exists
    only in another company is simply not found here.

    This is the shape that matters: the sheet names a DEPARTMENT, not an id, so
    there is nothing to inject -- and the resolution still cannot reach across
    the boundary.
    """
    from apps.employees.models import Employee
    from apps.organization.models import Department
    from core.access.catalog import DepartmentKind
    from core.middleware import acting_as

    with acting_as(None, organization=org_b.organization):
        Department.objects.create(
            name="Globex Only", code="GLX", kind=DepartmentKind.OPERATIONS
        )

    batch = create_employee_batch(
        actor=hr,
        file=employee_xlsx(
            rows=[
                _row(
                    "Cross Tenant", "cross@acme.test",
                    department="Globex Only", manager=manager_code,
                )
            ]
        ),
    )
    result = commit_employee_batch(actor=hr, batch=batch)

    assert result.created == 0 and result.failed == 1
    assert not Employee.objects.filter(user__email="cross@acme.test").exists()
    row = ImportRow.objects.get(batch=batch, row_number=2)
    assert {e["code"] for e in row.errors} == {"unknown_department"}


def test_an_employee_batch_is_not_a_candidate_batch(
    hr, structure, employee_xlsx, two_staff
):
    """
    One model, two kinds, and the candidate wizard must not offer a staff list.

    Its rows would be read as applicants and committed against a job opening
    the batch does not have.
    """
    from apps.imports.models import ImportBatch

    batch = create_employee_batch(actor=hr, file=employee_xlsx(rows=two_staff))

    candidate_batches = ImportBatch.objects.filter(kind=ImportKind.CANDIDATES)
    assert batch.pk not in {b.pk for b in candidate_batches}


def test_seed_roles_grants_bulk_hiring_to_the_roles_that_hire(organization):
    """
    The permission cell is new, so this asserts it actually seeds -- a matrix
    entry nobody re-seeds is a grant that exists only in the source file.
    """
    from apps.accounts.models import RolePermission
    from core.access import Action, Resource

    call_command("seed_roles", organization=organization.slug, verbosity=0)

    holders = set(
        RolePermission.objects.all_orgs()
        .filter(
            organization=organization,
            resource=Resource.EMPLOYEE,
            action=Action.IMPORT,
            is_active=True,
        )
        .values_list("role__code", flat=True)
    )
    assert {"admin", "hr_head", "hr_manager"} <= holders
    assert "employee" not in holders, "self-service must not bulk hire"
