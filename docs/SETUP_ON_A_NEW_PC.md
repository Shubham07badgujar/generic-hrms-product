# Setting Up on a New PC (for manual testing)

From a copy of this code to a running HRMS full of test data, in about
fifteen minutes. Every command below was run on a clean database while
writing this page.

> **The code alone is not enough.** A zip of the source carries no database,
> no `.env` and no installed packages — the users live in PostgreSQL, not in
> the files. That is what the steps below create. You will get the same
> *company and structure* as any other machine, but **new passwords**, printed
> into a credentials file at the end.

---

## 1. Install the prerequisites

| Software | Version | Notes |
|---|---|---|
| **Python** | 3.12 | Tick *"Add python.exe to PATH"* in the Windows installer |
| **PostgreSQL** | 16 | Remember the password you set for the `postgres` user |
| **Node.js** | 20 LTS | Includes npm |
| **Git** | any | Optional, only if you clone rather than copy a zip |
| **Redis** | 7 | **Optional** — only for background jobs; the app runs fully without it |

Check they are visible:

```bash
python --version
node --version
psql --version
```

## 2. Put the code somewhere

Unzip (or clone) so you have a folder like `D:\generic-hrms-product`
containing `apps\`, `docs\`, `deploy\`.

## 3. Create the database

```bash
psql -U postgres -c "CREATE USER hrms WITH PASSWORD 'choose-a-password';"
psql -U postgres -c "CREATE DATABASE hrms OWNER hrms;"
```

## 4. Install the backend

```bash
cd apps\api
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements\dev.txt
```

(macOS/Linux: `.venv/bin/python` throughout.)

## 5. Create `apps\api\.env`

Copy the template and fill it in:

```bash
copy .env.example .env
```

At minimum, set these four. Generate the two keys with the commands shown —
do not invent them by hand, and do not reuse another machine's:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
SECRET_KEY=<first command's output>
FIELD_ENCRYPTION_KEY=<second command's output>
DATABASE_URL=postgres://hrms:choose-a-password@127.0.0.1:5432/hrms
FRONTEND_URL=http://localhost:5173
```

Leave the rest at their defaults for testing. Email is fine unconfigured —
messages print to the API console instead of being sent, which is useful.

> `FIELD_ENCRYPTION_KEY` encrypts PAN/Aadhaar/bank numbers. On a test machine
> losing it costs nothing; on a real one it makes that data unrecoverable, so
> back it up there.

## 6. Create the tables and all the test data — one command

```bash
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py seed_all
```

`seed_all` is the single seed file. It runs every configuration seed in
order, creates the demo company with one account per role, and then lays
down transactional data so **no screen is empty**:

| Area | What you get |
|---|---|
| Organisation | 4 departments, 16 designations, 5 levels, 1 location, 18 roles + full permission matrix |
| People | 16 employees, one per role, plus CEO and Admin system accounts |
| Payroll | 4 salary components; 3 employees on structures spanning the PF/ESI/PT thresholds; 6 statutory rate sets **as drafts** |
| Attendance | The current month for 3 employees, including a late arrival and an absence; 3 device-ID mappings |
| Leave | 3 leave types with policies, balances, and 2 requests — one **awaiting a decision** |
| Assets | 3 assets registered, 1 in someone's hands |
| Recruitment | 1 published job with a public application link, 3 candidates with applications |
| Onboarding | 1 new joiner **still behind the onboarding gate** |

Useful variants:

```bash
manage.py seed_all --company "Acme Traders"   # a different fictional company
manage.py seed_all --reset                     # wipe the demo and rebuild
manage.py seed_all --skip-transactions         # config and people only
```

It refuses to run on a database holding non-demo employees unless you pass
`--force`.

## 7. Install and start the frontend

```bash
cd ..\web
npm install
npm run dev
```

## 8. Start the API (second terminal)

```bash
cd apps\api
.venv\Scripts\python.exe manage.py runserver
```

Open **http://localhost:5173** — the login screen should show the demo
company's name, read live from the database.

> Start order matters only in that the SPA proxies `/api` to port 8000: if
> the API is not up, every screen shows a connection error.

---

## Signing in

Passwords are in **`apps\api\media\demo-credentials.txt`**, one line per role.
Every account is forced to choose a new password on first sign-in — that is
the product working, not a bug.

Good places to start:

| Sign in as | To test |
|---|---|
| `hr_head@…` | Employees, onboarding approval, leave decisions, recruitment, attendance corrections |
| `finance_head@…` | Payroll approval, payslip deletion window, certifying statutory rates |
| `payroll_executive@…` | Preparing a payroll run (and being refused approval — segregation of duties) |
| `recruiter@…` | Jobs, candidates, the pipeline |
| `therapist@…` | Self-service: own attendance, leave, payslips |
| `new.joiner@…` (password `Onboard!Demo-2026`) | The onboarding gate — every module refused until HR approves the documents |
| `admin@…` | Organisation settings, roles and permissions, branding/logo |

## A deliberate first task: payroll

The statutory rate sets are seeded as **drafts on purpose**. Sign in as the
Finance Head, go to **Payroll → Settings**, and certify them — then a payroll
run can be approved. This mirrors what a real company must do, and it is one
of the more interesting things to test.

Then: as Payroll Executive create and process a run; as Finance Head approve
it (the preparer cannot — that refusal is the point); download a payslip;
delete one inside the five-day window.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `Set the SECRET_KEY environment variable` | `.env` missing or in the wrong folder — it belongs in `apps\api\` |
| `connection refused` / `could not connect to server` | PostgreSQL is not running, or `DATABASE_URL` is wrong |
| `No admin account exists` | Run `manage.py seed_all`, not the sub-seeds individually |
| Login page says "HRMS" with no company | `seed_all` has not run, or ran against a different database |
| Screens load but every request fails | The API is not running on port 8000 |
| `npm run dev`: vite not found | `npm install` was not run in `apps\web` |
| Emails "not sent" | Expected without SMTP — look for them in the API console |

Running it inside VS Code, with debugging:
[`RUNNING_IN_VS_CODE.md`](RUNNING_IN_VS_CODE.md).
Configuring it for a **real** company instead of the demo:
[`NEW_COMPANY_SETUP.md`](NEW_COMPANY_SETUP.md).
