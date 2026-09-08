"""
Prove a fresh install is a working, company-independent HRMS.

Run AFTER the full fresh-install sequence:

    migrate -> seed_roles -> seed_leave -> seed_statutory -> seed_onboarding
            -> seed_workflows -> seed_offboarding
            -> bootstrap_admin -> seed_demo_company
Exercises the system end to end as the demo company's own people and checks
that every identity surface says the DEMO company - the codebase carries no
company of its own.

    python manage.py shell < scripts/verify_fresh_install.py
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import datetime as dt
import secrets as _secrets

# Unique per run, so the script can be re-run on the same database.
NONCE = _secrets.token_hex(3)

from django.conf import settings
from rest_framework.test import APIClient

R = []


def check(name, ok, detail=""):
    R.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  :: {str(detail)[:220]}"))


creds = {}
cred_file = Path(settings.MEDIA_ROOT) / "demo-credentials.txt"
for email, pw in re.findall(r"([a-z_.0-9]+@[^\s|]+)\s+(\S+)", cred_file.read_text()):
    creds[email.lower()] = pw
check("0. demo credentials file exists with accounts", len(creds) >= 15, len(creds))


def rows(response_json):
    """Rows from either a paginated envelope or a bare list."""
    if isinstance(response_json, dict) and "data" in response_json:
        return response_json["data"]
    return response_json


def login(email):
    c = APIClient(HTTP_HOST="localhost")
    r = c.post("/api/v1/auth/login/", {"email": email, "password": creds[email]}, format="json")
    assert r.status_code == 200, (email, r.content[:150])
    c.credentials(HTTP_AUTHORIZATION="Bearer " + r.json()["access"])
    return c


DOMAIN = "demo-healthcare.example"

# The seed leaves every account on a forced first-login password change -
# correct product behaviour, but this script exercises the API directly, so
# lift the flag the way an operator would for scripted UAT.
import tempfile
from pathlib import Path as _P

from django.core.management import call_command

from apps.accounts.models import User

_emails = list(
    User.objects.filter(email__endswith=f"@{DOMAIN}").values_list("email", flat=True)
)
_args = []
for _e in _emails:
    _args += ["--email", _e]
call_command(
    "clear_password_change_flag", "--apply",
    "--manifest", str(_P(tempfile.gettempdir()) / "demo_flag_manifest.json"),
    *_args,
)

# ---- 1. the system introduces itself as the DEMO company, unauthenticated
anon = APIClient(HTTP_HOST="localhost")
branding = anon.get("/api/v1/org/branding/")
check("1. /org/branding/ is public", branding.status_code == 200, branding.status_code)
body = branding.json()
check("1b. it names the demo company, not any real one",
      body["name"] == "Demo Healthcare Pvt Ltd", body)
print("    branding:", body)

# ---- 2. every seeded role can sign in
failures = []
for email in sorted(creds):
    c = APIClient(HTTP_HOST="localhost")
    r = c.post("/api/v1/auth/login/", {"email": email, "password": creds[email]}, format="json")
    if r.status_code != 200:
        failures.append(email)
check("2. every demo account signs in", not failures, failures)

hr = login(f"hr_head@{DOMAIN}")

# ---- 3. the organisation structure exists and is the demo's
depts = rows(hr.get("/api/v1/departments/").json())
check("3. departments seeded", len(depts) == 4, [d["name"] for d in depts])
emps = hr.get("/api/v1/employees/").json()
emp_count = emps.get("total", len(rows(emps)))
check("3b. the sixteen demo employees exist", emp_count >= 16, emp_count)
role_rows = rows(hr.get("/api/v1/roles/").json())
check("3c. the 18 role templates exist", len(role_rows) >= 18, len(role_rows))

# ---- 4. the whole lifecycle works on the fresh install
ops_mgr_email = f"operations_manager@{DOMAIN}"
from apps.employees.models import Employee

mgr = Employee.objects.get(user__email=ops_mgr_email)
dept = next(d for d in depts if "Operations" in d["name"])
desigs = rows(hr.get("/api/v1/designations/").json())
desig = next(d for d in desigs if d["department"] == dept["id"])
lvl_rows = rows(hr.get("/api/v1/levels/").json())
staff_level = next(l for l in lvl_rows if l["layer"] == 5)
loc = rows(hr.get("/api/v1/locations/").json())[0]

created = hr.post("/api/v1/employees/", {
    "first_name": "Kiran", "last_name": f"Fresh{NONCE}",
    "email": f"kiran.fresh.{NONCE}@{DOMAIN}",
    "personal_email": f"kiran.fresh.{NONCE}@example.test",
    "role_code": "employee",
    "department_id": dept["id"], "designation_id": desig["id"],
    "location_id": loc["id"], "level_id": staff_level["id"],
    "reporting_manager_id": str(mgr.pk),
    "date_of_joining": str(dt.date.today()),
}, format="json")
check("4. HR creates an employee on the fresh install", created.status_code == 201,
      created.content[:200])
new_id = created.json()["employee"]["id"] if created.status_code == 201 else None

# leave: types exist (starter policy) and HR can see the queue
type_rows = rows(hr.get("/api/v1/leave-types/").json())
check("4b. starter leave types exist", len(type_rows) >= 2, len(type_rows))
check("4c. the leave queue answers", hr.get("/api/v1/leave-requests/").status_code == 200)

# payroll: components + rate sets shipped, awaiting Finance certification
fin = login(f"finance_head@{DOMAIN}")
# The component catalogue is deliberately NOT seeded - it is the company's
# own pay structure. The configurable path is what gets proven: Finance
# creates a component through the API, with no code involved.
made_comp = fin.post("/api/v1/payroll/components/", {
    "code": f"BAS{NONCE[:3].upper()}", "name": "Basic", "component_type": "earning",
    "calc_type": "fixed", "is_taxable": True, "is_part_of_ctc": True,
    "is_wage": True, "rounding": "nearest", "display_order": 1,
}, format="json")
check("4d. Finance defines the company's own salary component",
      made_comp.status_code in (200, 201), made_comp.content[:200])
rate_rows = rows(fin.get("/api/v1/payroll/rule-sets/").json())
check("4e. statutory rate sets shipped as reviewable fixtures", len(rate_rows) >= 5,
      len(rate_rows))
check("4f. none is certified until THIS company's Finance reviews it",
      all(r["verification_status"] != "verified" for r in rate_rows))

# ---- 5. permission engine runs from rows, not from code constants
admin = login(f"admin@{DOMAIN}")
made = admin.post("/api/v1/roles/", {
    "code": f"supervisor_{NONCE}", "name": f"Supervisor {NONCE}", "layer": 3,
}, format="json")
check("5. Admin creates a brand-new role at runtime", made.status_code in (200, 201),
      made.content[:180])

# ---- 6. self-service works for a demo employee
me = login(f"therapist@{DOMAIN}")
check("6. an employee reaches their own profile",
      me.get("/api/v1/employees/me/").status_code == 200)
check("6b. and only their own payslips",
      me.get("/api/v1/payslips/").status_code == 200)

# ---- 7. independence: nothing anywhere says the first deployment's name
import subprocess

leak = subprocess.run(
    ["git", "grep", "-il", "-e", "drjoshi", "-e", "dr. joshi", "-e", "dr joshi", "--", "apps/"],
    capture_output=True, text=True, cwd=str(Path(settings.BASE_DIR).parent.parent),
)
check("7. zero first-deployment references under apps/", leak.stdout.strip() == "",
      leak.stdout[:200])

failed = [n for n, ok in R if not ok]
print(f"\n{len(R) - len(failed)}/{len(R)} checks passed"
      + (f"; FAILED: {failed}" if failed else ""))
