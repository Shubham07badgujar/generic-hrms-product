"""
A customer's whole record, as a file they can keep.

WHY THIS EXISTS. The lifecycle promises that a CANCELLED organization can still
export its data for a window, and until now that promise was unkept: a
non-operational organization's users resolve to a grant-less context, so every
route -- every export included -- was closed to them the moment the
subscription was cancelled. A customer obliged to retain payslips and
attendance for years was locked out of their own statutory records by a
commercial decision. "Export before deletion is a right, not a courtesy."

ONE ROUTE, NOT A READ-ONLY API. The obvious alternative -- let a cancelled
organization keep VIEW and EXPORT everywhere -- is exactly what the
suspension gate refuses on purpose: a stopped organization's data is
preserved, not published. So the exception is one endpoint, which decides for
itself who and when, and everything else stays shut.

WHO: an active holder of the ADMIN role in their own organization. Tested by
role code, which the product otherwise never does -- and legitimately here,
because Admin is a system role (`is_grantable=False`, created only by
bootstrap or provisioning), not one of the customer's editable defaults. The
grant matrix cannot be used: in a cancelled organization it resolves to
nothing, which is the point of the gate.

WHEN: while the organization is operational, or CANCELLED and within
`EXPORT_WINDOW_DAYS` of the cancellation. Not SUSPENDED -- restoring is the
remedy there, and an export would publish what suspension preserves. Not
ARCHIVED.

WHAT: `MANIFEST`, written out rather than derived. Adding a model to the
product must never silently widen what leaves in this file -- the same reason
`CEO_READABLE` is enumerated.

WITHHELD: every encrypted column. PAN, Aadhaar and bank account numbers are
statutory identifiers, and a bulk file of them is a liability the customer did
not ask for by clicking "download" -- Aadhaar numbers in particular carry legal
restrictions on storage and sharing. Integration credentials are secrets, not
records, and are never exported at all. The README inside the archive names
every withheld column, so the omission is visible to the person holding it.

TENANCY: every read goes through the tenant manager, bound to the caller's own
organization. No `all_orgs()` anywhere in this file; a row from another
organization cannot appear because the manager will not return it.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta

from django.apps import apps
from django.utils import timezone

from core.fields import EncryptedCharField
from core.middleware import acting_as

#: How long after cancellation the customer may still take their records.
EXPORT_WINDOW_DAYS = 90

#: (file name inside the archive, model label). Order is the reading order.
MANIFEST: tuple[tuple[str, str], ...] = (
    ("organization/departments.csv", "organization.Department"),
    ("organization/locations.csv", "organization.Location"),
    ("organization/designations.csv", "organization.Designation"),
    ("organization/levels.csv", "organization.EmployeeLevel"),
    ("employees/employees.csv", "employees.Employee"),
    ("employees/addresses.csv", "employees.EmployeeAddress"),
    ("employees/emergency_contacts.csv", "employees.EmergencyContact"),
    ("employees/education.csv", "employees.EmployeeEducation"),
    ("employees/experience.csv", "employees.EmployeeExperience"),
    ("employees/document_types.csv", "employees.DocumentType"),
    ("employees/documents.csv", "employees.EmployeeDocument"),
    ("employees/probation_reviews.csv", "employees.ProbationReview"),
    ("attendance/records.csv", "attendance.AttendanceRecord"),
    ("attendance/regularizations.csv", "attendance.RegularizationRequest"),
    ("leave/types.csv", "leave.LeaveType"),
    ("leave/balances.csv", "leave.LeaveBalance"),
    ("leave/requests.csv", "leave.LeaveRequest"),
    ("leave/transactions.csv", "leave.LeaveTransaction"),
    ("leave/holidays.csv", "leave.Holiday"),
    ("payroll/components.csv", "payroll.SalaryComponent"),
    ("payroll/salary_structures.csv", "payroll.SalaryStructure"),
    ("payroll/salary_structure_lines.csv", "payroll.SalaryStructureLine"),
    ("payroll/runs.csv", "payroll.PayrollRun"),
    ("payroll/payslips.csv", "payroll.Payslip"),
    ("payroll/payslip_lines.csv", "payroll.PayslipLine"),
    ("payroll/statutory_contributions.csv", "payroll.StatutoryContribution"),
    ("payroll/adjustments.csv", "payroll.PayrollAdjustment"),
    ("payroll/loans.csv", "payroll.EmployeeLoan"),
    ("payroll/reimbursements.csv", "payroll.ReimbursementClaim"),
)

#: Named in the README so an omission is a sentence, not a surprise.
NOT_INCLUDED = (
    "Recruitment (candidates and applications): applicants are not employees, "
    "and their records follow their own retention schedule.",
    "The audit trail: it is retained by the platform beyond the organization's "
    "own lifetime, and can be requested from support.",
    "Uploaded files (document scans, attachments): documents.csv lists each "
    "one with its original file name, size and status; the files themselves "
    "are not in this archive.",
)


class ExportRefused(Exception):
    """Well-formed, permitted to ask, and not allowed right now. Answered 422."""


@dataclass
class ExportResult:
    filename: str
    content: bytes
    rows: dict[str, int] = field(default_factory=dict)
    withheld: dict[str, list[str]] = field(default_factory=dict)


def _is_admin(user, organization) -> bool:
    from apps.accounts.models import UserRole
    from core.access.catalog import RoleCode

    with acting_as(None, organization=organization):
        return UserRole.objects.filter(
            user=user, role__code=RoleCode.ADMIN, is_active=True
        ).exists()


def _cancelled_at(organization):
    from apps.platform.models import Subscription

    subscription = (
        Subscription.objects.filter(organization=organization, is_active=True)
        .only("cancelled_at")
        .first()
    )
    return subscription.cancelled_at if subscription else None


def refusal_for(user) -> str | None:
    """
    Why this user may not export right now, or None when they may.

    The ONE decision, used by the route and by `/me/` alike, so the button the
    SPA shows and the answer the route gives cannot disagree.
    """
    from apps.organization.membership import organization_of
    from apps.organization.models import OPERATIONAL_STATUSES, OrgStatus

    if getattr(user, "is_platform_admin", False):
        # Not an oversight: the platform has no implicit reach into customer
        # data, and a whole-organization export is the widest reach there is.
        return "Platform administrators do not export customer data."
    organization = organization_of(user)
    if organization is None:
        return "You do not belong to an organization."
    if not _is_admin(user, organization):
        return "Only your organization's Admin can export its data."

    if organization.status in OPERATIONAL_STATUSES:
        return None
    if organization.status == OrgStatus.CANCELLED:
        cancelled_at = _cancelled_at(organization)
        if cancelled_at is None:
            return None
        deadline = cancelled_at + timedelta(days=EXPORT_WINDOW_DAYS)
        if timezone.now() <= deadline:
            return None
        return (
            f"The export window closed on {deadline:%d %b %Y}, "
            f"{EXPORT_WINDOW_DAYS} days after cancellation."
        )
    if organization.status == OrgStatus.SUSPENDED:
        return (
            "This organization is suspended. Its data is preserved; restoring "
            "the account restores access to it."
        )
    return "This organization's data is no longer available for export."


def _columns(model):
    withheld, kept = [], []
    for model_field in model._meta.concrete_fields:
        if isinstance(model_field, EncryptedCharField):
            withheld.append(model_field.name)
        else:
            kept.append(model_field)
    return kept, withheld


def _cell(value) -> str:
    """
    One CSV cell, neutralised against spreadsheet formula injection.

    A value starting with `=`, `+`, `-` or `@` is executed by Excel as a
    formula when the file is opened. The archive's contents were typed by the
    customer's own users and applicants, so the same neutralisation the import
    path applies on the way IN is applied here on the way out.
    """
    if value is None:
        return ""
    text = value.name if hasattr(value, "name") and hasattr(value, "url") else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _members_csv(organization) -> tuple[str, int]:
    """
    The organization's users, so every `*_by_id` and `user_id` column joins to
    a name. Identity only -- no password hash, no flags beyond active.
    """
    from apps.accounts.models import User

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "email", "first_name", "last_name", "is_active"])
    count = 0
    for user in (
        User.objects.filter(memberships__organization=organization)
        .distinct()
        .order_by("email")
        .iterator()
    ):
        writer.writerow(
            [_cell(user.pk), _cell(user.email), _cell(user.first_name),
             _cell(user.last_name), _cell(user.is_active)]
        )
        count += 1
    return buffer.getvalue(), count


def _readme(organization, result: ExportResult) -> str:
    lines = [
        f"{organization.name} -- data export",
        f"Generated {timezone.now():%Y-%m-%d %H:%M} UTC",
        "",
        "One CSV per record type. Columns ending in _id are identifiers that",
        "join across files; members.csv resolves the user ids. Rows with",
        "is_active=False were removed in the application and are kept here",
        "because they are records too.",
        "",
        "Rows per file:",
    ]
    lines += [f"  {name}: {count}" for name, count in result.rows.items()]
    lines += ["", "Withheld columns (encrypted at rest, not exported in bulk):"]
    if result.withheld:
        lines += [
            f"  {name}: {', '.join(columns)}"
            for name, columns in result.withheld.items()
        ]
    else:
        lines += ["  none"]
    lines += ["", "Not included:"]
    lines += [f"  - {item}" for item in NOT_INCLUDED]
    return "\n".join(lines) + "\n"


def build_export(user) -> ExportResult:
    """
    The archive for this user's organization, or `ExportRefused`.

    Built in memory, synchronously. Right for the companies this product
    serves today; a very large one would want a background job and a link,
    which is noted as the next step rather than pretended away.
    """
    from apps.audit.events import record_event
    from apps.organization.membership import organization_of

    reason = refusal_for(user)
    if reason is not None:
        raise ExportRefused(reason)
    organization = organization_of(user)

    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    result = ExportResult(filename=f"{organization.slug}-export-{stamp}.zip", content=b"")
    archive_buffer = io.BytesIO()

    with acting_as(user, organization=organization), zipfile.ZipFile(
        archive_buffer, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        members, count = _members_csv(organization)
        archive.writestr("members.csv", members)
        result.rows["members.csv"] = count

        for name, label in MANIFEST:
            model = apps.get_model(label)
            kept, withheld = _columns(model)
            if withheld:
                result.withheld[name] = withheld

            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow([f.attname for f in kept])
            count = 0
            # The tenant manager, bound above. Not `all_orgs()`: the rows that
            # can appear here are the rows this organization owns, and the
            # manager is what makes that true.
            for row in model.objects.all().order_by("pk").values_list(
                *[f.attname for f in kept]
            ).iterator(chunk_size=2000):
                writer.writerow([_cell(value) for value in row])
                count += 1
            archive.writestr(name, buffer.getvalue())
            result.rows[name] = count

        archive.writestr("README.txt", _readme(organization, result))

        # The customer's own trail: who took the whole record, and when. An
        # export is the largest data-egress event the product has.
        record_event(
            organization,
            actor=user,
            entity_type="organization.Organization",
            verb="export",
            resource="",
            after={
                "event": "organization_export",
                "status": organization.status,
                "rows": result.rows,
                "withheld": sorted(result.withheld),
            },
        )

    result.content = archive_buffer.getvalue()
    return result
