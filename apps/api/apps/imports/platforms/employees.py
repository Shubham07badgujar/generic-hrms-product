"""
The staff list: an HR spreadsheet, not a job-board export.

A company arriving on the platform already has its people somewhere — usually
one sheet from the previous system, a payroll provider, or a finance
department. This is the adapter for that sheet, and it reuses the whole intake
path: `core.validators` for the bytes, `parsing` for the walk, `ImportBatch`
for the staging and the two-phase commit.

WHY IT IS A `PlatformSpec` AT ALL. It describes no external platform — the
"platform" is the customer's own spreadsheet. But the spec is what the parser
reads: aliases, per-column parsers, required fields. Describing employee
columns the same way means the employee path branches on nothing; the parser
cannot tell the difference, and there is no second walk to keep in step with
the first.

WHAT IT DELIBERATELY DOES NOT CARRY. No salary, no bank account, no
identifiers. Those are payroll's, entered under payroll's permissions and
protected by payroll's redaction — a column here called `pan` would put a
national identifier into a staging table with a shorter retention clock and a
weaker permission gate than the one the domain uses. Structure and identity
only.

REFERENCES ARE NAMES, RESOLVED AT COMMIT. A sheet says "People" and
"Pune Office", not two UUIDs, because the person filling it in has never seen
a UUID. Those names are resolved against THIS organization's departments and
locations when the batch commits -- so a name that matches nothing is a row
error the operator can fix, and a name can never reach another company's
department, which is the whole reason the resolution is not done from the id
in the cell.
"""

from __future__ import annotations

from .parsers import iso_date
from .registry import ColumnSpec, PlatformSpec

#: What an employee row may name. Distinct from the candidate set: these are
#: the arguments `create_employee` actually takes.
EMPLOYEE_FIELDS = (
    "employee_code",
    "first_name",
    "middle_name",
    "last_name",
    "full_name",
    "email",
    "personal_email",
    "phone",
    "department",
    "designation",
    "location",
    "level",
    "team",
    "reporting_manager",
    "role_code",
    "employment_type",
    "date_of_joining",
)

EMPLOYEES = PlatformSpec(
    key="employees",
    label="Employee list (your own spreadsheet)",
    canonical_fields=EMPLOYEE_FIELDS,
    columns=(
        # Optional: a company migrating from another system usually has its own
        # numbering and wants to keep it. Left out, the organization's counter
        # allocates one.
        ColumnSpec("employee_code", ("employee code", "employee id", "emp code", "emp id", "staff id")),
        ColumnSpec("full_name", ("name", "full name", "employee name", "staff name")),
        ColumnSpec("first_name", ("first name", "firstname", "given name")),
        ColumnSpec("middle_name", ("middle name", "middlename")),
        ColumnSpec("last_name", ("last name", "lastname", "surname", "family name")),
        # The work address becomes their login, so it is required and must be
        # unique platform-wide -- the message when it is not says so.
        ColumnSpec("email", ("email", "work email", "official email", "company email", "email address")),
        # Where the credentials actually go. A new joiner cannot read their own
        # company mailbox before they can sign in to create it.
        ColumnSpec("personal_email", ("personal email", "private email", "alternate email")),
        ColumnSpec("phone", ("phone", "mobile", "mobile no", "mobile number", "contact", "contact number")),
        ColumnSpec("department", ("department", "dept", "department name", "function")),
        ColumnSpec("designation", ("designation", "title", "job title", "position")),
        ColumnSpec("location", ("location", "office", "branch", "work location", "site")),
        ColumnSpec("level", ("level", "grade", "band", "employee level")),
        ColumnSpec("team", ("team", "sub team", "squad")),
        ColumnSpec("reporting_manager", ("reporting manager", "manager", "reports to", "supervisor")),
        ColumnSpec("role_code", ("role", "system role", "access role")),
        ColumnSpec("employment_type", ("employment type", "type", "engagement", "contract type")),
        ColumnSpec("date_of_joining", ("date of joining", "joining date", "doj", "start date", "hire date"), iso_date),
    ),
    # A person needs a name, a work address that becomes their login, a
    # department to sit in and a day they started. Everything else the product
    # can default or leave empty; without these four there is no employee to
    # create, and a row missing one is reported rather than guessed at.
    required=frozenset({"first_name", "email", "department", "date_of_joining"}),
    notes=(
        "Your own spreadsheet, one row per person. Columns are matched by "
        "name, and anything unrecognised is listed back so you can map it by "
        "hand. Departments, designations, locations, levels and managers are "
        "matched by NAME against this organization -- create them first, or "
        "fix the spelling and re-upload. Salary and bank details are not "
        "imported here; they belong to payroll."
    ),
)
