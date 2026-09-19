"""
Importing a company's staff list.

The same two-phase intake the candidate importer uses, pointed at a different
destination: upload parses and stages, commit writes, and the commit
re-resolves everything against the database rather than replaying the preview's
verdict. What differs is what a row becomes — an Employee, their login, their
role grant and their onboarding checklist — and three consequences of that.

SEATS ARE CHECKED FOR THE WHOLE BATCH, BEFORE THE FIRST WRITE
-------------------------------------------------------------
`create_employee` reserves one seat per hire under a row lock, which is right
for one hire and wrong for two hundred: a 200-row file against 150 remaining
seats would import 150 people and then start failing, leaving a half-migrated
staff list that somebody has to reconcile by hand. So the batch reserves for
every committable row up front and refuses as a whole. A refused import is an
operator reading one message; a partial one is a data-cleanup project.

THE EMPLOYEE-CODE COUNTER IS LOCKED ONCE
----------------------------------------
`next_employee_code()` takes `SELECT FOR UPDATE` on the organization's settings
row. Postgres holds that lock until the transaction ends, so running the whole
batch inside ONE transaction means the row is locked once and every later
allocation is uncontended — rather than two hundred lock-and-release cycles
interleaving with anybody else hiring. It also means the codes in one import
are consecutive, which is what the person reading the spreadsheet afterwards
expects.

NOBODY IS EMAILED FROM INSIDE THE TRANSACTION
---------------------------------------------
`create_employee` normally schedules a welcome email per hire, each opening its
own SMTP conversation bounded by `EMAIL_TIMEOUT` (15s). Two hundred of those is
up to fifty minutes of a blocked worker, and every one of them fires while the
import transaction is still open. Here the hires are made with
`send_welcome_email=False` and ONE task is queued after commit, which sends
them all over a single connection.

The credentials cannot travel with that task. A temporary password exists in
memory and is stored nowhere; putting two hundred of them in a Celery payload
would write live credentials into the broker, where they would sit in Redis
and in whatever inspects it. The task generates each password at send time
instead — the same thing `reissue_credentials` does for one person.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.imports.models import (
    BatchStatus,
    ImportBatch,
    ImportKind,
    ImportRow,
    RowStatus,
)
from apps.imports.platforms.employees import EMPLOYEES
from apps.imports.platforms.parsers import is_ambiguous_date

#: Shared with the candidate importer deliberately: the same upload limits, the
#: same accepted extensions, the same audit and counter helpers. A staff list is
#: not a more trustworthy file because it came from the customer's own finance
#: department, and two copies of the commit bookkeeping would be two things to
#: keep in step.
from apps.imports.services.importer import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    _audit,
    _refresh_counters,
)
from apps.imports.services.parsing import parse
from core.access import Action, Resource, require
from core.access.context import get_context
from core.api.exceptions import BusinessRuleError
from core.models import TenancyError
from core.phone import to_e164_in
from core.validators import validate_upload

logger = logging.getLogger("hrms.imports")

#: Default when a sheet does not say. Every organization seeds an `employee`
#: role, and it is the least authority anyone can hold -- an import must never
#: be the thing that hands out more.
DEFAULT_ROLE_CODE = "employee"


class RowRefused(Exception):
    """
    One row cannot be hired, with a machine-readable reason.

    Its own type, carrying a code, rather than a message another function
    re-reads: an error code recovered by searching an exception's text is a
    code that changes when somebody improves the wording.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class EmployeeImportResult:
    batch: ImportBatch
    created: int = 0
    failed: int = 0
    employee_ids: list = field(default_factory=list)


# ------------------------------------------------------------------ upload


def _identity_fields(parsed: dict) -> dict:
    """The columns `ImportRow` stores for anyone, candidate or employee."""
    email = (parsed.get("email") or "").strip().lower() or None
    phone = (parsed.get("phone") or "").strip()
    first = (parsed.get("first_name") or "").strip()
    last = (parsed.get("last_name") or "").strip()

    # A single "name" column is how most staff lists are written. Split once,
    # here, so the rest of the path sees the same shape as a sheet that had
    # two columns.
    if not first and (parsed.get("full_name") or "").strip():
        parts = parsed["full_name"].strip().split()
        first = parts[0]
        last = last or (" ".join(parts[1:]) if len(parts) > 1 else "")

    return {
        "first_name": first[:100],
        "last_name": last[:100],
        "email": email,
        "email_normalized": email,
        "phone": phone[:40],
        "phone_e164": to_e164_in(phone),
    }


def _stage_row(row: ImportRow, parsed: dict, *, seen_emails: dict) -> None:
    """
    What this row looks like before anything is written.

    Advisory, every one of it. The commit re-resolves each reference against
    the database, because between a preview and a commit somebody can rename a
    department or hire the person in row 12 by hand. What this pass is for is
    letting an operator fix a 200-row sheet in one pass instead of discovering
    the twelfth problem after the eleventh fix.
    """
    from apps.accounts.models import User

    errors: list[dict] = []
    warnings: list[dict] = []

    if not row.first_name:
        errors.append({"row": row.row_number, "column": "name", "code": "missing_name"})
    if not row.email_normalized:
        errors.append({"row": row.row_number, "column": "email", "code": "missing_email"})
    if not (parsed.get("department") or "").strip():
        errors.append(
            {"row": row.row_number, "column": "department", "code": "missing_department"}
        )
    if parsed.get("date_of_joining") is None:
        errors.append(
            {
                "row": row.row_number,
                "column": "date_of_joining",
                "code": "missing_or_unreadable_date",
            }
        )

    # The work address becomes a login, and logins are unique across the whole
    # platform -- so this is checked against every organization's users, which
    # is also the one place that rule becomes visible to an operator.
    if row.email_normalized:
        if row.email_normalized in seen_emails:
            row.status = RowStatus.DUPLICATE_IN_FILE
            row.duplicate_of_row = seen_emails[row.email_normalized]
            row.errors = errors
            row.warnings = warnings
            return
        seen_emails[row.email_normalized] = row.row_number
        if User.objects.filter(email=row.email_normalized).exists():
            errors.append(
                {"row": row.row_number, "column": "email", "code": "email_already_has_login"}
            )

    # Who this person reports to, when their role needs one. Layer 2 heads
    # legitimately answer to nobody with an Employee record; everyone below
    # them must have a manager, and `create_employee` refuses without one. The
    # check is repeated here so a 200-row sheet says so at preview rather than
    # failing two hundred times at commit -- and the rule itself is still
    # enforced where it belongs, in the hierarchy service.
    if not (parsed.get("reporting_manager") or "").strip() and _needs_manager(
        (parsed.get("role_code") or "").strip() or DEFAULT_ROLE_CODE
    ):
        errors.append(
            {
                "row": row.row_number,
                "column": "reporting_manager",
                "code": "missing_reporting_manager",
            }
        )

    # A date that two readers would read differently. Day-first is applied --
    # the house convention, and what the rest of the product renders -- and the
    # row says so, because a joining date sets probation, accrual and the first
    # payroll period.
    raw_date = (row.raw or {}).get(_header_for(row, "date_of_joining"), "")
    if is_ambiguous_date(raw_date):
        warnings.append(
            {
                "row": row.row_number,
                "column": "date_of_joining",
                "code": "ambiguous_date_read_day_first",
            }
        )

    row.errors = errors
    row.warnings = warnings
    row.status = RowStatus.INVALID if errors else RowStatus.VALID


def _needs_manager(role_code: str) -> bool:
    """
    Whether a hire in this role must name somebody to report to.

    Reads the role's layer rather than a list of codes: roles are runtime-
    editable and an organization may rename or replace every one of them, so a
    check against `role.code` would answer for the seeded set and nothing else.
    An unknown code is treated as needing a manager -- the safe direction,
    since the commit will refuse it anyway and saying so at preview is the
    whole point of this pass.
    """
    from apps.accounts.models import Role
    from core.access.catalog import Layer

    role = Role.objects.filter(code=role_code, is_active=True).first()
    if role is None:
        return True
    return role.layer > Layer.DEPARTMENT_HEAD


def _header_for(row: ImportRow, canonical: str) -> str:
    """The original column name a canonical field came from, or ''."""
    return (row.batch.column_mapping or {}).get(canonical, "")


@transaction.atomic
def create_employee_batch(
    *, actor, file, column_override: dict | None = None
) -> ImportBatch:
    """Validate, parse and stage a staff list. Writes no employees and no logins."""
    require(actor, Resource.EMPLOYEE, Action.IMPORT)

    facts = validate_upload(
        file,
        allowed_extensions=ALLOWED_EXTENSIONS,
        max_bytes=MAX_UPLOAD_BYTES,
        subject="spreadsheet",
    )
    parsed = parse(file, facts=facts, spec=EMPLOYEES, column_override=column_override)

    batch = ImportBatch.objects.create(
        kind=ImportKind.EMPLOYEES,
        platform=EMPLOYEES.key,
        job_opening=None,
        uploaded_by=actor,
        original_filename=facts.original_name,
        file_sha256=facts.sha256,
        file_size_bytes=facts.size_bytes,
        declared_content_type=facts.declared_content_type,
        column_mapping=parsed.mapping,
        detected_headers=parsed.headers,
        unmapped_headers=parsed.unmapped_headers,
        rows_total=len(parsed.rows),
        created_by=actor,
    )

    seen_emails: dict[str, int] = {}
    rows: list[ImportRow] = []
    for parsed_row in parsed.rows:
        row = ImportRow(
            batch=batch,
            row_number=parsed_row["_row"],
            raw=parsed_row.get("_raw", {}),
            **_identity_fields(parsed_row),
        )
        _stage_row(row, parsed_row, seen_emails=seen_emails)
        rows.append(row)

    ImportRow.objects.bulk_create(rows, batch_size=500)
    _refresh_counters(batch)

    # Counts and hashes. No cell content: this line goes to container logs,
    # which are not a PII store.
    logger.info(
        "import.employee_preview batch=%s actor=%s rows=%s sha=%s",
        batch.pk, actor.pk, batch.rows_total, facts.sha256[:12],
    )
    _audit(batch, actor=actor, verb="import", extra={"phase": "preview", "kind": "employees"})
    return batch


# ------------------------------------------------------------------ commit


def committable(batch: ImportBatch):
    """Rows a commit would attempt. The seat arithmetic counts these."""
    return ImportRow.objects.filter(
        batch=batch, status__in=[RowStatus.VALID, RowStatus.PENDING]
    )


def commit_employee_batch(*, actor, batch: ImportBatch) -> EmployeeImportResult:
    """
    Hire everyone in the batch, or refuse the batch.

    Re-authorised, re-counted and re-resolved here: the preview ran at some
    earlier moment, under permissions that may since have been revoked, against
    departments that may since have been renamed.
    """
    require(actor, Resource.EMPLOYEE, Action.IMPORT)

    if batch.kind != ImportKind.EMPLOYEES:
        raise BusinessRuleError("This batch is a candidate import.")
    if batch.status in (BatchStatus.COMPLETED, BatchStatus.PARTIAL):
        # Already done. Idempotent rather than an error, so a retried request
        # reports what happened instead of refusing.
        return _result_from(batch)
    if batch.status == BatchStatus.DISCARDED:
        raise BusinessRuleError("This import was discarded.")

    organization_id = get_context(actor).organization_id
    pending = list(committable(batch).order_by("row_number"))
    if not pending:
        raise BusinessRuleError("No rows in this import can be committed.")

    result = EmployeeImportResult(batch=batch)

    # ONE transaction for the whole batch: it is what holds the employee-code
    # counter's lock once rather than per row, and what makes the seat
    # reservation below mean something for every row rather than the first.
    with transaction.atomic():
        _reserve_for_batch(organization_id, count=len(pending))

        batch.status = BatchStatus.COMMITTING
        batch.save(update_fields=["status", "updated_at"])

        for row in pending:
            _commit_row(row, actor=actor, batch=batch, result=result)

        _refresh_counters(batch)
        batch.refresh_from_db()
        batch.status = (
            BatchStatus.PARTIAL if batch.rows_failed else BatchStatus.COMPLETED
        )
        batch.committed_at = timezone.now()
        batch.save(update_fields=["status", "committed_at", "updated_at"])

        # After commit, never inside it: the transaction still holds the
        # counter lock, and an SMTP conversation is not something to do while
        # holding a lock other hires are waiting on.
        if result.employee_ids:
            transaction.on_commit(
                lambda: _queue_welcome_emails(organization_id, batch.pk)
            )

    logger.info(
        "import.employee_commit batch=%s actor=%s created=%s failed=%s",
        batch.pk, actor.pk, result.created, result.failed,
    )
    _audit(
        batch,
        actor=actor,
        verb="import",
        extra={"phase": "commit", "kind": "employees", "created": result.created},
    )
    return result


def _reserve_for_batch(organization_id, *, count: int) -> None:
    """
    Seats for the WHOLE batch, before anything is written.

    `create_employee` reserves one at a time, which would import until the
    limit and then fail row by row -- a half-migrated staff list nobody asked
    for. `SeatLimitReached` is allowed to propagate: it is a 422 with its own
    code, and the message names the limit and the count, so the operator can
    see at a glance whether to trim the file or buy seats.
    """
    from apps.platform.services.subscriptions import reserve_seats

    reserve_seats(organization_id, count=count)


def _commit_row(row: ImportRow, *, actor, batch: ImportBatch, result) -> None:
    """
    One hire, in its own savepoint.

    A row that fails -- a department nobody can find, an address taken between
    preview and commit -- marks itself FAILED and leaves the rest of the batch
    alone. The savepoint is what makes that true: without it, one bad row would
    roll back the whole transaction, and a 200-row import would be decided by
    its worst line.
    """
    from apps.employees.services.creation import create_employee

    try:
        with transaction.atomic():
            fields = _resolve_row(row, batch=batch)
            created = create_employee(
                actor=actor,
                send_welcome_email=False,  # one batched send, after commit
                **fields,
            )
            row.created_employee = created.employee
            row.status = RowStatus.CREATED
            row.errors = []
            result.created += 1
            result.employee_ids.append(str(created.employee.pk))
    except TenancyError:
        # Never swallowed into a per-row error. A tenancy failure is not a bad
        # spreadsheet cell; it is the isolation layer refusing, and it must
        # stop the import rather than be reported as row 14 being malformed.
        raise
    except Exception as exc:  # noqa: BLE001 -- one bad row must not end the batch
        row.status = RowStatus.FAILED
        row.errors = [
            {"row": row.row_number, "code": _error_code(exc)},
        ]
        result.failed += 1
        logger.info(
            "import.employee_row_failed batch=%s row=%s code=%s",
            batch.pk, row.row_number, _error_code(exc),
        )

    # No `updated_at`: `ImportRow` is a TimestampedModel, which carries
    # `created_at` alone. Staging rows have no update history worth keeping --
    # the batch owns them and the retention job removes them outright.
    row.save(update_fields=["status", "errors", "created_employee"])


def _resolve_row(row: ImportRow, *, batch: ImportBatch) -> dict:
    """
    A staged row -> `create_employee` arguments, resolved NOW.

    References arrive as names, because that is what a person types into a
    spreadsheet. They are resolved against this organization's own rows through
    the tenant manager, so a name that exists in another company simply does
    not resolve here -- the id never travels, and there is nothing to inject.
    """
    from apps.organization.models import (
        Department,
        Designation,
        EmployeeLevel,
        Location,
        Team,
    )

    raw = row.raw or {}

    def cell(canonical: str) -> str:
        header = (batch.column_mapping or {}).get(canonical, "")
        return (raw.get(header) or "").strip() if header else ""

    def by_name(model, value: str, field_name: str = "name"):
        if not value:
            return None
        return model.objects.filter(**{f"{field_name}__iexact": value}).first()

    department = by_name(Department, cell("department"))
    if department is None:
        raise RowRefused("unknown_department")

    date_of_joining = _parsed_date(cell("date_of_joining"))
    if date_of_joining is None:
        raise RowRefused("missing_or_unreadable_date")

    designation = by_name(Designation, cell("designation"), "title")
    location = by_name(Location, cell("location"))
    level = by_name(EmployeeLevel, cell("level"))
    team = by_name(Team, cell("team"))

    manager_name = cell("reporting_manager")
    manager = None
    if manager_name:
        manager = _manager_by_name(manager_name)
        if manager is None:
            raise RowRefused("unknown_reporting_manager")

    return {
        "first_name": row.first_name,
        "middle_name": cell("middle_name")[:100],
        "last_name": row.last_name,
        "email": row.email_normalized,
        "personal_email": cell("personal_email"),
        "phone": row.phone,
        "role_code": cell("role_code") or DEFAULT_ROLE_CODE,
        "department_id": department.pk,
        "designation_id": designation.pk if designation else None,
        "location_id": location.pk if location else None,
        "level_id": level.pk if level else None,
        "team_id": team.pk if team else None,
        "reporting_manager_id": manager.pk if manager else None,
        "employment_type": cell("employment_type") or "full_time",
        "date_of_joining": date_of_joining,
        "employee_code": cell("employee_code") or None,
        # A designation is optional in a staff list: plenty of companies track
        # one grade and no title. The product's own hire form requires it, and
        # relaxing it here is a deliberate difference, not an oversight.
        "require_designation": False,
    }


def _manager_by_name(value: str):
    """The one active employee this name identifies, or None if it is not one."""
    from django.db.models import Q

    from apps.employees.models import Employee

    parts = value.split()
    query = Q(first_name__iexact=value) | Q(employee_code__iexact=value)
    if len(parts) > 1:
        query |= Q(first_name__iexact=parts[0], last_name__iexact=" ".join(parts[1:]))

    matches = list(Employee.objects.filter(is_active=True).filter(query)[:2])
    # Two people called Priya is an ambiguity a machine must not resolve: the
    # wrong manager is an approval chain pointing at a stranger.
    return matches[0] if len(matches) == 1 else None


def _parsed_date(value):
    from apps.imports.platforms.parsers import iso_date

    return iso_date(value)


def _error_code(exc) -> str:
    """A machine-readable code, never the offending value."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    if isinstance(exc, RowRefused):
        return exc.code
    if isinstance(exc, BusinessRuleError):
        return getattr(exc, "default_code", "refused")
    if isinstance(exc, DjangoValidationError):
        # The FIELD it refused, not the sentence it refused with: a code has to
        # survive somebody improving the wording, and `invalid_row` told an
        # operator nothing about which cell to look at.
        fields = getattr(exc, "message_dict", {}) or {}
        return f"invalid_{next(iter(fields))}" if fields else "invalid_row"
    return type(exc).__name__


def _result_from(batch: ImportBatch) -> EmployeeImportResult:
    return EmployeeImportResult(
        batch=batch,
        created=batch.rows_created,
        failed=batch.rows_failed,
        employee_ids=[
            str(pk)
            for pk in ImportRow.objects.filter(
                batch=batch, created_employee__isnull=False
            ).values_list("created_employee_id", flat=True)
        ],
    )


def _queue_welcome_emails(organization_id, batch_id) -> None:
    from apps.imports.tasks import send_import_welcome_emails

    send_import_welcome_emails.delay(str(organization_id), str(batch_id))
