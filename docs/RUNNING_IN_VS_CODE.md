# Running the HRMS in VS Code

Everything below is verified working on this machine. The app is already
installed and seeded with the demo company — if you are starting from a fresh
clone instead, do the one-time setup in
[`NEW_COMPANY_SETUP.md`](NEW_COMPANY_SETUP.md) first.

---

## Open the project

**File → Open Folder → `D:\generic-hrms-product`**

Open it at that top level, not at `apps/api` or `apps/web` — you want both
halves in one window.

### Recommended extensions

| Extension | Why |
|---|---|
| **Python** (Microsoft) | Runs and debugs Django, picks up the virtualenv |
| **Ruff** | The linter this project is configured for (`pyproject.toml`) |
| **ESLint** + **Prettier** | Frontend linting and formatting |
| **Tailwind CSS IntelliSense** | Class-name completion in the React code |

### Point VS Code at the right Python

`Ctrl+Shift+P` → **Python: Select Interpreter** → **Enter interpreter path** →

```
D:\generic-hrms-product\apps\api\.venv\Scripts\python.exe
```

Without this, imports show as unresolved and the debugger will not start.

---

## Run it — two terminals

`` Ctrl+` `` opens the terminal; the **`+`** button (or `Ctrl+Shift+5`) splits
it so you can watch both at once.

### Terminal 1 — the API

```
cd apps\api
.venv\Scripts\python.exe manage.py runserver
```

Wait for `Starting development server at http://127.0.0.1:8000/`.

### Terminal 2 — the web app

```
cd apps\web
npm run dev
```

Wait for `Local: http://localhost:5173/`, then **Ctrl+click that link**.

> The frontend proxies `/api` to port 8000, so **the API must be running** or
> every screen will show a connection error. Start Terminal 1 first.

---

## Sign in

The demo accounts and their passwords are in:

```
apps\api\media\demo-credentials.txt
```

One account per role. To explore as HR, use the `hr_head@demo-healthcare.example`
row; for full configuration screens use `admin@…`.

Each account is forced to set a new password on first sign-in — that is the
product working as intended. Pick anything that meets the policy.

---

## Optional: background jobs

Only needed for scheduled work (attendance sync, reminders, retention purges).
Requires Redis on `localhost:6379`. Two more terminals:

```
cd apps\api
.venv\Scripts\python.exe -m celery -A config worker -l info --pool=solo
```

```
cd apps\api
.venv\Scripts\python.exe -m celery -A config beat -l info
```

The app is fully usable without these.

---

## Debugging (optional but worth it)

Create `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Django API",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/apps/api/manage.py",
      "args": ["runserver", "--noreload"],
      "cwd": "${workspaceFolder}/apps/api",
      "django": true,
      "justMyCode": false
    }
  ]
}
```

Then **F5** starts the API with breakpoints working. `--noreload` is required —
the auto-reloader forks and loses the debugger.

---

## Running the tests

```
cd apps\api
.venv\Scripts\python.exe -m pytest -q
```

```
cd apps\web
npx vitest run
```

One backend test is **designed to fail** — `test_no_unresolved_statutory_disputes`
lists statutory rate questions your finance function must answer before payroll
is certified. A failure there is the system doing its job, not a broken test.

---

## Stopping

`Ctrl+C` in each terminal.

---

## Common problems

| Symptom | Cause and fix |
|---|---|
| `ImproperlyConfigured: Set the SECRET_KEY environment variable` | `apps/api/.env` is missing. Copy `.env.example` to `.env` and fill it in. |
| Screens load but every request fails | The API is not running, or is not on port 8000. |
| `connection refused` on the database | PostgreSQL is not running, or `DATABASE_URL` in `.env` is wrong. |
| Imports unresolved / debugger will not start | The interpreter is not set — see *Point VS Code at the right Python*. |
| Login page says "HRMS" instead of a company name | No Organisation Settings row yet. Run `seed_demo_company`, or set the name in the app under Organisation → Settings. |
| `npm run dev` cannot find vite | `npm install` was not run in `apps/web`. |
