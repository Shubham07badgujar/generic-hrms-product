"""
Three fictional companies, as data.

WHY THREE, AND WHY DIFFERENT. One demo company proves the product runs. It
proves nothing at all about a multi-tenant product, because every question
worth asking of one -- does organization A's HR Head see organization B's
payroll, does a plan without payroll actually hide payroll, does a trial that
is about to expire say so -- needs a second and a third company that differ
from the first. So these three differ on purpose, on the axes the SaaS
mechanisms run along:

    Healthcare   20 employees   5 departments   3 locations   every module
    Technology   15 employees   4 departments   2 locations   payroll disabled
    Retail       10 employees   3 departments   2 locations   trial, expiring

Healthcare is the one with COMPLETE ROLE COVERAGE -- one signed-in-able account
for every role in the seeded template -- and it is deliberately the largest for
that reason: it is the profile RBAC work is tested against. Fifteen people
cannot cover eighteen roles and ten certainly cannot, so the other two carry
one account per role THEY CONTAIN, and the roles they leave out are named in
`absent_roles` rather than quietly missing. A test asserting "every role has an
account" would otherwise be measuring Technology against a promise only
Healthcare makes.

WHAT A PROFILE MAY NOT CONTAIN. Nothing here is a real person, a real company,
a real address or a reachable email: every domain is under `.example`, which
RFC 2606 reserves precisely so that it can never resolve. The names are
invented. This matters more than it sounds -- demo people in a live HR database
show up in headcount, in BI and in payroll runs, so they have to be
unmistakable at a glance and greppable in one command.

WHAT CONSTRAINS THE SHAPE. These are not free-form fixtures: every person here
is created through `create_employee`, which enforces the same rules a real hire
goes through. Three of them decide the layout below:

  * `ROLE_DEPARTMENT_KINDS` -- a functional role must sit in its own function.
    An HR Head cannot be placed in an Engineering department, so Technology's
    People department is `HR` KIND with an industry NAME. The kind is
    taxonomy; the name is the customer's word for it.
  * A manager must be at the same layer or more senior, and only a department
    head may have none. So `people` is ordered with every manager ahead of
    their reports, and `validate()` checks that rather than trusting it.
  * `executive`, `office_boy` and `employee` are department-agnostic, which is
    what lets an `OTHER`-kind department exist at all.

ROLE NAMES ARE RENAMED, NOT REDEFINED. Each profile may carry `role_names`,
applied to that organization's own `Role` rows. It changes what the roles
screen calls them and nothing else -- the permission matrix, the layers and
the segregation-of-duties invariants are code, identical in all three. That is
the product's actual stance ("these are defaults you own, not system roles"),
and a demo that never exercised it would be demonstrating a claim nobody had
tested. What it is NOT is a per-industry permission model; there is one matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.access.catalog import DepartmentKind, Layer, RoleCode

#: The two principals with no Employee record (`requires_employee=False`).
#: Every profile has them: an organization without an administrator cannot be
#: administered, and the CEO dashboard is a screen somebody has to be able to
#: open.
SYSTEM_PEOPLE = (
    ("ceo", "Vikram", "Malhotra"),
    ("admin", "Sysadmin", "Demo"),
)

#: The five seniority bands. Identical everywhere, because the five-layer
#: authority model is code rather than preference -- a profile that invented a
#: sixth band would be describing a different product.
LEVELS = (
    ("L1", "Leadership", Layer.LEADERSHIP, 10),
    ("L2", "Department Head", Layer.DEPARTMENT_HEAD, 20),
    ("L3", "Manager", Layer.MANAGER, 30),
    ("L4", "Executive", Layer.EXECUTIVE, 40),
    ("L5", "Staff", Layer.STAFF, 50),
)


@dataclass(frozen=True)
class DemoLocation:
    code: str
    name: str
    city: str
    #: NOT cosmetic: Professional Tax is a state levy, so a location without a
    #: state means PT silently computes to nothing.
    state: str
    is_head_office: bool = False


@dataclass(frozen=True)
class DemoDepartment:
    code: str
    #: What this company calls it.
    name: str
    #: What the product calls it. Drives dashboards and the role/department
    #: compatibility rule; never authorization scope.
    kind: str
    designations: tuple[str, ...]
    #: Username of its head, set after everyone exists. None is legitimate --
    #: a department may be headed by whoever runs the function above it.
    head: str | None = None


@dataclass(frozen=True)
class DemoPerson:
    #: The email local part, and the key everything else refers to. Equal to
    #: the role code wherever a profile has exactly one person in that role,
    #: which keeps `admin@…`, `hr_head@…` and the rest addressable by role --
    #: the property the original single-company demo was built around.
    username: str
    role_code: str
    first_name: str
    last_name: str
    #: Department CODE, not kind: a profile may hold two departments of the
    #: same kind, and keying by kind silently merged them.
    department: str
    level: Layer
    designation: str
    #: Username of the reporting manager. None only for department heads.
    manager: str | None = None
    #: Location CODE. None means the head office.
    location: str | None = None


@dataclass(frozen=True)
class DemoProfile:
    key: str
    company: str
    legal_name: str
    slug: str
    #: Under `.example`, which cannot resolve. Also the removal key.
    domain: str
    #: Which plan this company is sold. The three differ so that entitlement,
    #: seat limits and trial expiry are all exercisable by hand.
    plan_code: str
    departments: tuple[DemoDepartment, ...]
    locations: tuple[DemoLocation, ...]
    people: tuple[DemoPerson, ...]
    #: What this profile demonstrates, printed by the seeding command so the
    #: person running it knows which company to open for which question.
    purpose: str = ""
    #: Per-organization role RENAMES. Display only -- see the module docstring.
    role_names: dict[str, str] = field(default_factory=dict)
    system_people: tuple[tuple[str, str, str], ...] = SYSTEM_PEOPLE

    # -- derived ----------------------------------------------------------
    @property
    def head_office(self) -> DemoLocation:
        for location in self.locations:
            if location.is_head_office:
                return location
        return self.locations[0]

    @property
    def roles_present(self) -> set[str]:
        return {person.role_code for person in self.people} | {
            code for code, _first, _last in self.system_people
        }

    @property
    def absent_roles(self) -> set[str]:
        """
        Roles the seeded template carries that this company has nobody in.
        Named rather than implied: "one account per role" is Healthcare's
        promise, and the other two must not be measured against it.
        """
        template = {
            value
            for name, value in vars(RoleCode).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        return template - self.roles_present

    # -- validation -------------------------------------------------------
    def validate(self) -> list[str]:
        """
        Everything that would otherwise fail halfway through a seeding run.

        Checked as DATA, so a malformed profile is a test failure and a refusal
        at the top of the command rather than eleven employees created and the
        twelfth raising `HierarchyError` inside a transaction that then rolls
        all eleven back.
        """
        from apps.accounts.permission_matrix import ROLE_SPECS
        from apps.employees.services.hierarchy import ROLE_DEPARTMENT_KINDS

        specs = {spec.code: spec for spec in ROLE_SPECS}
        problems: list[str] = []
        departments = {d.code: d for d in self.departments}
        locations = {location.code: location for location in self.locations}
        usernames: set[str] = set()

        if len([location for location in self.locations if location.is_head_office]) != 1:
            problems.append(f"{self.key}: exactly one location must be the head office")

        seen_so_far: set[str] = set()
        for person in self.people:
            where = f"{self.key}/{person.username}"
            if person.username in usernames:
                problems.append(f"{where}: duplicate username")
            usernames.add(person.username)

            spec = specs.get(person.role_code)
            if spec is None:
                problems.append(f"{where}: {person.role_code!r} is not a template role")
            else:
                # `assert_role_matches_level` refuses a band that disagrees
                # with the role's own layer, and `create_employee` refuses a
                # role that has no place in the hierarchy at all.
                if spec.layer != person.level:
                    problems.append(
                        f"{where}: {person.role_code!r} sits at layer {spec.layer}, "
                        f"not {int(person.level)}"
                    )
                if not spec.requires_employee:
                    problems.append(
                        f"{where}: {person.role_code!r} is a system role and has no "
                        f"place in the employee hierarchy"
                    )

            department = departments.get(person.department)
            if department is None:
                problems.append(f"{where}: unknown department {person.department!r}")
            else:
                if person.designation not in department.designations:
                    problems.append(
                        f"{where}: designation {person.designation!r} is not one of "
                        f"{department.code}'s"
                    )
                allowed = ROLE_DEPARTMENT_KINDS.get(person.role_code)
                if allowed is not None and department.kind not in allowed:
                    problems.append(
                        f"{where}: role {person.role_code!r} cannot sit in a "
                        f"{department.kind!r} department"
                    )

            if person.location is not None and person.location not in locations:
                problems.append(f"{where}: unknown location {person.location!r}")

            # Manager rules, as `assert_reporting_manager_is_valid` applies
            # them: only a department head may have none, and the manager has
            # to exist ALREADY -- the seeding loop creates people in order.
            if person.manager is None:
                if person.level > Layer.DEPARTMENT_HEAD:
                    problems.append(f"{where}: below department head, so needs a manager")
            elif person.manager not in seen_so_far:
                problems.append(
                    f"{where}: manager {person.manager!r} is not created before them"
                )
            else:
                manager = next(p for p in self.people if p.username == person.manager)
                if manager.level > person.level:
                    problems.append(
                        f"{where}: reports to {manager.username!r}, who is more junior"
                    )
            seen_so_far.add(person.username)

        for department in self.departments:
            if department.head is not None and department.head not in usernames:
                problems.append(
                    f"{self.key}/{department.code}: head {department.head!r} is not in the roster"
                )

        for code, _first, _last in self.system_people:
            if code in usernames:
                problems.append(f"{self.key}: {code!r} is both a system principal and an employee")
            spec = specs.get(code)
            if spec is None:
                problems.append(f"{self.key}: {code!r} is not a template role")
            elif spec.requires_employee:
                problems.append(
                    f"{self.key}: {code!r} needs an Employee record, so it is not a "
                    f"system principal"
                )

        # A rename that collides is refused by the unique constraint on
        # `Role.name` per organization, halfway through seeding.
        renamed = list(self.role_names.values())
        if len(set(renamed)) != len(renamed):
            problems.append(f"{self.key}: two roles renamed to the same name")
        for code in self.role_names:
            if code not in specs:
                problems.append(f"{self.key}: cannot rename {code!r} -- no such role")

        return problems


# ---------------------------------------------------------------------------
# Healthcare — the complete-coverage profile
# ---------------------------------------------------------------------------

HEALTHCARE = DemoProfile(
    key="healthcare",
    company="Demo Healthcare Pvt Ltd",
    legal_name="Demo Healthcare Private Limited",
    slug="demo-healthcare",
    domain="demo-healthcare.example",
    #: Every module, no seat limit. The profile RBAC and payroll work is tested
    #: against, so nothing it needs may be switched off by entitlement.
    plan_code="enterprise",
    purpose=(
        "Every module enabled and one account per role — the profile to test "
        "permissions, payroll and the full hierarchy against."
    ),
    locations=(
        DemoLocation("HO", "Head Office", "Pune", "MH", is_head_office=True),
        DemoLocation("BLR", "Bengaluru Clinic", "Bengaluru", "KA"),
        DemoLocation("MUM", "Mumbai Clinic", "Mumbai", "MH"),
    ),
    departments=(
        DemoDepartment(
            "MED", "Medical", DepartmentKind.MEDICAL,
            ("Medical Director", "Senior Consultant", "Clinic Doctor", "Therapist"),
            head="medical_director",
        ),
        DemoDepartment(
            "OPS", "Operations", DepartmentKind.OPERATIONS,
            ("Head of Operations", "Operations Manager",
             "Customer Relations Executive", "Executive", "Facilities Assistant",
             "Associate"),
            head="operational_head",
        ),
        DemoDepartment(
            "HR", "Human Resources", DepartmentKind.HR,
            ("HR Head", "HR Manager", "Talent Acquisition Specialist"),
            head="hr_head",
        ),
        DemoDepartment(
            "FIN", "Finance & Accounts", DepartmentKind.FINANCE,
            ("Finance Head", "Accounts Manager", "Payroll Executive"),
            head="finance_head",
        ),
        # Fifth department, and headless on purpose: it reports into
        # Operations. A department may be run by the function above it, and a
        # profile that gave every department a head would be hiding that.
        DemoDepartment(
            "ADM", "Administration", DepartmentKind.OTHER,
            ("Administration Assistant", "Records Assistant"),
        ),
    ),
    people=(
        # --- Layer 2. No internal manager: they answer to the CEO, who has no
        # Employee record to point at.
        DemoPerson("medical_director", RoleCode.MEDICAL_DIRECTOR, "Meera", "Kulkarni",
                   "MED", Layer.DEPARTMENT_HEAD, "Medical Director"),
        DemoPerson("operational_head", RoleCode.OPERATIONAL_HEAD, "Oindrila", "Sen",
                   "OPS", Layer.DEPARTMENT_HEAD, "Head of Operations"),
        DemoPerson("hr_head", RoleCode.HR_HEAD, "Hema", "Rao",
                   "HR", Layer.DEPARTMENT_HEAD, "HR Head"),
        DemoPerson("finance_head", RoleCode.FINANCE_HEAD, "Farah", "Khan",
                   "FIN", Layer.DEPARTMENT_HEAD, "Finance Head"),

        # --- Layer 3.
        DemoPerson("senior_doctor", RoleCode.SENIOR_DOCTOR, "Sanjay", "Iyer",
                   "MED", Layer.MANAGER, "Senior Consultant", "medical_director"),
        DemoPerson("operations_manager", RoleCode.OPERATIONS_MANAGER, "Omkar", "Patil",
                   "OPS", Layer.MANAGER, "Operations Manager", "operational_head"),
        DemoPerson("hr_manager", RoleCode.HR_MANAGER, "Hari", "Menon",
                   "HR", Layer.MANAGER, "HR Manager", "hr_head"),
        DemoPerson("accounts_manager", RoleCode.ACCOUNTS_MANAGER, "Anita", "Kelkar",
                   "FIN", Layer.MANAGER, "Accounts Manager", "finance_head"),

        # --- Layer 4.
        DemoPerson("clinic_doctor", RoleCode.CLINIC_DOCTOR, "Chandni", "Bose",
                   "MED", Layer.EXECUTIVE, "Clinic Doctor", "senior_doctor"),
        DemoPerson("cre", RoleCode.CRE, "Chetan", "Desai",
                   "OPS", Layer.EXECUTIVE, "Customer Relations Executive",
                   "operations_manager"),
        DemoPerson("executive", RoleCode.EXECUTIVE, "Esha", "Kapoor",
                   "OPS", Layer.EXECUTIVE, "Executive", "operations_manager"),
        DemoPerson("recruiter", RoleCode.RECRUITER, "Ravi", "Shah",
                   "HR", Layer.EXECUTIVE, "Talent Acquisition Specialist", "hr_manager"),
        DemoPerson("payroll_executive", RoleCode.PAYROLL_EXECUTIVE, "Pooja", "Reddy",
                   "FIN", Layer.EXECUTIVE, "Payroll Executive", "accounts_manager"),

        # --- Layer 5.
        DemoPerson("therapist", RoleCode.THERAPIST, "Tara", "Nair",
                   "MED", Layer.STAFF, "Therapist", "senior_doctor"),
        DemoPerson("office_boy", RoleCode.OFFICE_BOY, "Om", "Jadhav",
                   "OPS", Layer.STAFF, "Facilities Assistant", "operations_manager"),
        DemoPerson("employee", RoleCode.EMPLOYEE, "Ekta", "Sharma",
                   "OPS", Layer.STAFF, "Associate", "operations_manager"),

        # --- The four that take this company past "one per role" to a shape
        # worth looking at: a second clinic, a second site, and a department
        # nobody heads. A company where every role has exactly one holder
        # cannot exercise a team view, a rota or a department roster.
        DemoPerson("clinic_doctor_blr", RoleCode.CLINIC_DOCTOR, "Kavya", "Prasad",
                   "MED", Layer.EXECUTIVE, "Clinic Doctor", "senior_doctor",
                   location="BLR"),
        DemoPerson("therapist_mum", RoleCode.THERAPIST, "Tanvi", "Joshi",
                   "MED", Layer.STAFF, "Therapist", "senior_doctor", location="MUM"),
        DemoPerson("cre_blr", RoleCode.CRE, "Charu", "Nanda",
                   "OPS", Layer.EXECUTIVE, "Customer Relations Executive",
                   "operations_manager", location="BLR"),
        DemoPerson("records_assistant", RoleCode.EMPLOYEE, "Rehan", "Qureshi",
                   "ADM", Layer.STAFF, "Records Assistant", "operational_head"),
    ),
)


# ---------------------------------------------------------------------------
# Technology — the profile with a module switched off
# ---------------------------------------------------------------------------

TECHNOLOGY = DemoProfile(
    key="technology",
    company="Demo Technology Labs Pvt Ltd",
    legal_name="Demo Technology Labs Private Limited",
    slug="demo-technology",
    domain="demo-technology.example",
    #: Starter, whose disabled list includes PAYROLL. Signing in here as the
    #: Finance Head and finding no payroll is the point: entitlement is not a
    #: permission, and the two must be visibly different things.
    plan_code="starter",
    purpose=(
        "On a plan without payroll (or assets, biometric devices, IT accounts "
        "and reporting) — the company to open when the question is what a "
        "disabled module does."
    ),
    #: The departments are named for the industry; the KINDS underneath are the
    #: product's own taxonomy, and the roles are renamed to match. Same matrix,
    #: different words for it.
    role_names={
        RoleCode.OPERATIONAL_HEAD: "VP Engineering",
        RoleCode.OPERATIONS_MANAGER: "Engineering Manager",
        RoleCode.CRE: "Support Engineer",
        RoleCode.EXECUTIVE: "Software Engineer",
        RoleCode.EMPLOYEE: "Team Member",
        RoleCode.HR_HEAD: "Head of People",
        RoleCode.HR_MANAGER: "People Partner",
        RoleCode.RECRUITER: "Technical Recruiter",
    },
    locations=(
        DemoLocation("HO", "Bengaluru Office", "Bengaluru", "KA", is_head_office=True),
        DemoLocation("HYD", "Hyderabad Office", "Hyderabad", "TS"),
    ),
    departments=(
        DemoDepartment(
            "ENG", "Engineering", DepartmentKind.OPERATIONS,
            ("VP Engineering", "Engineering Manager", "Software Engineer",
             "Support Engineer", "QA Engineer"),
            head="eng_head",
        ),
        # `OTHER` kind, which is what lets a department exist that no
        # functional role is tied to. Only the department-agnostic roles --
        # executive, employee, office_boy -- may sit here, and that is a rule
        # of the product rather than of this file.
        DemoDepartment(
            "PRD", "Product", DepartmentKind.OTHER,
            ("Product Manager", "Product Analyst"),
            head="product_manager",
        ),
        DemoDepartment(
            "PPL", "People", DepartmentKind.HR,
            ("Head of People", "People Partner", "Technical Recruiter"),
            head="hr_head",
        ),
        DemoDepartment(
            "FIN", "Finance", DepartmentKind.FINANCE,
            ("Finance Head", "Accounts Manager", "Payroll Executive"),
            head="finance_head",
        ),
    ),
    people=(
        DemoPerson("eng_head", RoleCode.OPERATIONAL_HEAD, "Nikhil", "Varma",
                   "ENG", Layer.DEPARTMENT_HEAD, "VP Engineering"),
        DemoPerson("hr_head", RoleCode.HR_HEAD, "Priya", "Raghavan",
                   "PPL", Layer.DEPARTMENT_HEAD, "Head of People"),
        DemoPerson("finance_head", RoleCode.FINANCE_HEAD, "Deepak", "Bhatt",
                   "FIN", Layer.DEPARTMENT_HEAD, "Finance Head"),

        DemoPerson("eng_manager", RoleCode.OPERATIONS_MANAGER, "Arjun", "Pillai",
                   "ENG", Layer.MANAGER, "Engineering Manager", "eng_head"),
        DemoPerson("hr_manager", RoleCode.HR_MANAGER, "Sneha", "Kulkarni",
                   "PPL", Layer.MANAGER, "People Partner", "hr_head"),
        DemoPerson("accounts_manager", RoleCode.ACCOUNTS_MANAGER, "Rohit", "Saxena",
                   "FIN", Layer.MANAGER, "Accounts Manager", "finance_head"),

        # Cross-department reporting, which the hierarchy rules permit at any
        # seniority and which a real product organization actually has.
        DemoPerson("product_manager", RoleCode.EXECUTIVE, "Ananya", "Gupta",
                   "PRD", Layer.EXECUTIVE, "Product Manager", "eng_head"),
        DemoPerson("engineer_one", RoleCode.EXECUTIVE, "Vivek", "Menon",
                   "ENG", Layer.EXECUTIVE, "Software Engineer", "eng_manager"),
        DemoPerson("engineer_two", RoleCode.EXECUTIVE, "Ritu", "Chawla",
                   "ENG", Layer.EXECUTIVE, "Software Engineer", "eng_manager"),
        DemoPerson("engineer_three", RoleCode.EXECUTIVE, "Imran", "Sheikh",
                   "ENG", Layer.EXECUTIVE, "Software Engineer", "eng_manager",
                   location="HYD"),
        DemoPerson("support_engineer", RoleCode.CRE, "Neha", "Dubey",
                   "ENG", Layer.EXECUTIVE, "Support Engineer", "eng_manager",
                   location="HYD"),
        DemoPerson("recruiter", RoleCode.RECRUITER, "Aditya", "Nayar",
                   "PPL", Layer.EXECUTIVE, "Technical Recruiter", "hr_manager"),
        DemoPerson("payroll_executive", RoleCode.PAYROLL_EXECUTIVE, "Shruti", "Kale",
                   "FIN", Layer.EXECUTIVE, "Payroll Executive", "accounts_manager"),

        DemoPerson("qa_engineer", RoleCode.EMPLOYEE, "Manish", "Thakur",
                   "ENG", Layer.STAFF, "QA Engineer", "eng_manager"),
        DemoPerson("product_analyst", RoleCode.EMPLOYEE, "Leena", "Fernandes",
                   "PRD", Layer.STAFF, "Product Analyst", "product_manager"),
    ),
)


# ---------------------------------------------------------------------------
# Retail — the profile on a trial that is about to run out
# ---------------------------------------------------------------------------

RETAIL = DemoProfile(
    key="retail",
    company="Demo Retail Stores Pvt Ltd",
    legal_name="Demo Retail Stores Private Limited",
    slug="demo-retail",
    domain="demo-retail.example",
    #: A full-featured plan, so that what this company demonstrates is the
    #: TRIAL rather than a missing module: the subscription is `trialing` with
    #: an end date days away, which is the state a customer is in when somebody
    #: has to decide whether to buy.
    plan_code="growth",
    purpose=(
        "A trial days from expiry — the company to open when the question is "
        "what a customer sees as their trial runs out."
    ),
    role_names={
        RoleCode.OPERATIONAL_HEAD: "Head of Retail Operations",
        RoleCode.OPERATIONS_MANAGER: "Store Manager",
        RoleCode.CRE: "Sales Associate",
        RoleCode.EXECUTIVE: "Senior Sales Associate",
        RoleCode.OFFICE_BOY: "Stock Assistant",
        RoleCode.EMPLOYEE: "Cashier",
    },
    locations=(
        DemoLocation("HO", "Mumbai Store", "Mumbai", "MH", is_head_office=True),
        DemoLocation("PUN", "Pune Store", "Pune", "MH"),
    ),
    departments=(
        DemoDepartment(
            "STR", "Stores", DepartmentKind.OPERATIONS,
            ("Head of Retail Operations", "Store Manager", "Sales Associate",
             "Senior Sales Associate", "Stock Assistant", "Cashier"),
            head="ops_head",
        ),
        DemoDepartment(
            "HR", "People", DepartmentKind.HR,
            ("HR Head", "HR Manager"),
            head="hr_head",
        ),
        DemoDepartment(
            "FIN", "Finance", DepartmentKind.FINANCE,
            ("Finance Head", "Accounts Manager"),
            head="finance_head",
        ),
    ),
    people=(
        DemoPerson("ops_head", RoleCode.OPERATIONAL_HEAD, "Sameer", "Joshi",
                   "STR", Layer.DEPARTMENT_HEAD, "Head of Retail Operations"),
        DemoPerson("hr_head", RoleCode.HR_HEAD, "Rukmini", "Das",
                   "HR", Layer.DEPARTMENT_HEAD, "HR Head"),
        DemoPerson("finance_head", RoleCode.FINANCE_HEAD, "Yusuf", "Ali",
                   "FIN", Layer.DEPARTMENT_HEAD, "Finance Head"),

        DemoPerson("store_manager", RoleCode.OPERATIONS_MANAGER, "Kiran", "Bhosale",
                   "STR", Layer.MANAGER, "Store Manager", "ops_head"),
        DemoPerson("store_manager_pune", RoleCode.OPERATIONS_MANAGER, "Aarti", "Gokhale",
                   "STR", Layer.MANAGER, "Store Manager", "ops_head", location="PUN"),
        DemoPerson("hr_manager", RoleCode.HR_MANAGER, "Ishaan", "Verma",
                   "HR", Layer.MANAGER, "HR Manager", "hr_head"),
        DemoPerson("accounts_manager", RoleCode.ACCOUNTS_MANAGER, "Suman", "Bhatia",
                   "FIN", Layer.MANAGER, "Accounts Manager", "finance_head"),

        DemoPerson("sales_associate", RoleCode.CRE, "Pallavi", "Rane",
                   "STR", Layer.EXECUTIVE, "Sales Associate", "store_manager"),
        DemoPerson("senior_associate", RoleCode.EXECUTIVE, "Gaurav", "Salvi",
                   "STR", Layer.EXECUTIVE, "Senior Sales Associate",
                   "store_manager_pune", location="PUN"),
        DemoPerson("stock_assistant", RoleCode.OFFICE_BOY, "Bhavesh", "More",
                   "STR", Layer.STAFF, "Stock Assistant", "store_manager"),
    ),
)


PROFILES: dict[str, DemoProfile] = {
    profile.key: profile for profile in (HEALTHCARE, TECHNOLOGY, RETAIL)
}

#: The order they are seeded in, and the order they are listed in. Healthcare
#: first because it is the one most people want.
PROFILE_ORDER = ("healthcare", "technology", "retail")


def get_profile(key: str) -> DemoProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(
            f"{key!r} is not a demo profile. Known: {', '.join(PROFILE_ORDER)}."
        ) from None
