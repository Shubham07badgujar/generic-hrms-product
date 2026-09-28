# Deploying Generic HRMS on Render

For deploying **by hand, from your own machine**, to a Render account you own.
Generic HRMS is a separate product from the live `hrms.drjoshis.in`: nothing
here touches that system, its server, its database or its `deploy/` directory.

Files this uses:

| File | What it is |
|---|---|
| `render.yaml` | The blueprint: API, Celery worker, scheduler, SPA, Redis, Postgres |
| `render/build-api.sh` | Build step — dependencies, static files, deploy checks |
| `render/release-api.sh` | Migrations, **as the database owner**, plus `seed_plans` |
| `render/sql/create_runtime_role.sql` | Creates the confined runtime role, if the host allows it |

---

## Read this first: the one thing Render cannot give you

Release 2 of the tenancy hardening splits the database into two roles. The
application connects as `generic_hrms_app`, which **owns nothing**, because
**a table's owner is exempt from row-level security**. That is the whole point:
own the tables and the policies are decoration.

A managed Postgres gives you one user, and it owns the database. Creating a
second role needs `CREATEROLE`, and `BYPASSRLS` normally needs a superuser.
Render may not grant either. So decide which of these you are running, and know
what you have:

| | Single role (Render default) | Two roles (`create_runtime_role.sql` worked) |
|---|---|---|
| Composite foreign keys (151) | **enforced** | **enforced** |
| Audit scrub-only trigger | **enforced** | **enforced** |
| Audit DELETE/TRUNCATE revoked | no — the owner cannot be revoked from | **enforced** |
| Row-level security (100 tables) | **inert** — policies exist, owner exempt | **enforced** |
| Application-level isolation | enforced (unchanged) | enforced |

Single role is not "insecure" — it is the product as it shipped before this
hardening, with the tenant manager, `scope_queryset`, serializer relation
scoping and the system checks all doing their work. What you lose is the last
layer, the one that catches a raw query that forgets its filter. Run
single-role if you must, but do not tell yourself RLS is on. Verify with the
command in step 7.

---

## 1. Before you start

- A Render account, and this repository pushed to GitHub (branch
  `saas/multi-tenant`).
- **A Fernet key**, generated on your machine and kept somewhere safe:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  This encrypts PAN, Aadhaar and bank details. **Back it up outside the
  database.** Lose it and those columns are unreadable — there is no recovery.

- Somewhere for uploads to live. Render's filesystem is **ephemeral**: without
  object storage, every employee document, payslip PDF and letter is destroyed
  on the next deploy. Create an S3 (or S3-compatible) bucket, private, and have
  its name, region and credentials ready. The application warns at boot when
  this is missing; it cannot save you from it.

## 2. Create the blueprint

In Render: **New → Blueprint**, point it at your repository, pick the branch.
It reads `render.yaml` and proposes the services.

Set the variables marked `sync: false` **before the first deploy** — the build
runs `manage.py check --deploy`, and production refuses to boot without
`ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`. A failed first build here is the
setting doing its job.

| Variable | Value |
|---|---|
| `FIELD_ENCRYPTION_KEY` | the Fernet key from step 1 |
| `ALLOWED_HOSTS` | `hrms-web.onrender.com` (the SPA's host — requests arrive through its rewrite) |
| `CORS_ALLOWED_ORIGINS` | `https://hrms-web.onrender.com` |
| `FRONTEND_URL` | `https://hrms-web.onrender.com` |
| `S3_BUCKET`, `S3_REGION` | your bucket (and `S3_ENDPOINT` for a non-AWS provider) |
| `EMAIL_HOST`, `HR_EMAIL_HOST_USER`, `HR_EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | your SMTP details |

Leave `SENTRY_DSN` empty unless you use Sentry.

## 3. Why the SPA fronts the API

The blueprint gives the static site a rewrite: `/api/*` to the API service.
Keep it. The frontend requests `/api/v1/...` as a **relative** path, and the
refresh cookie is `SameSite=Strict`. Serve the SPA from one hostname and the
API from another and the browser will not send that cookie — people are signed
out the moment their 15-minute access token expires, with nothing in any log to
explain it.

If you rename the API service, update the rewrite's destination in
`render.yaml` to match.

## 4. First deploy

Apply the blueprint. The build installs dependencies, collects static files and
runs the deploy checks. It does **not** migrate — three services build in
parallel and must not race three migrations at one database.

## 5. Migrate

Open a **Shell** on `hrms-api` (or set the script as a pre-deploy command) and
run:

```bash
./../../render/release-api.sh
```

It points `DATABASE_URL` at `DATABASE_OWNER_URL` for the length of the
migration, then seeds the plan catalogue. Provisioning refuses to create a
customer without plans, so a fresh database needs this.

## 6. Split the roles (optional, and worth it)

Still in the shell, with a password you choose:

```bash
psql "$DATABASE_OWNER_URL" -v app_password="'a-strong-password'" \
     -f ../../render/sql/create_runtime_role.sql
```

If it succeeds:

1. Set `DATABASE_URL` on **all three** Python services to the same connection
   string but with `generic_hrms_app` and that password — host, port and
   database identical. Settings refuse to boot if the two URLs disagree about
   which database they mean.
2. Re-run the migration step, so `dbguard.0006` grants the new role its table
   privileges and revokes DELETE and TRUNCATE on the audit trail. That
   migration skips silently when the roles are absent, which is why it must run
   *after* the script.

If it fails with a permissions error, you are running single role. That is a
supported configuration — see the table at the top — and nothing else needs
changing.

## 7. Verify what you actually got

Do not assume. In the shell:

```bash
python manage.py shell -c "
from core.db_roles import current_database_role, schema_ownership
print('connected as:', current_database_role())
print('may change schema:', schema_ownership())
"
```

`connected as: generic_hrms_app` and `may change schema: (False, ...)` means the
split took and row-level security is live. `generic_hrms_owner` means it is
inert, whatever the migrations report.

Then create the first platform operator:

```bash
python manage.py bootstrap_platform_admin --email you@example.com
```

Sign in at `https://hrms-web.onrender.com`, and provision your first customer
from the platform console.

## 8. What to watch afterwards

- **Uploads.** If you skipped S3, confirm now what happens to a document after
  a redeploy. Finding out later is worse.
- **`ADMIN_BOOTSTRAP_TOKEN`.** Leave it unset. Set only to use the HTTP
  bootstrap route, and remove it immediately afterwards — the application warns
  while it is present, because it is a privileged endpoint.
- **One scheduler.** `hrms-beat` must stay at a single instance. Two schedulers
  means every accrual and reminder runs twice.
- **Performance.** Row-level security evaluates a policy per row across ~100
  tables; the test suite roughly doubled in duration when it went on. Request
  latency under RLS has **not** been measured. Watch your p95 after the first
  real load, and read the connection-pooler note in
  `docs/DB_HARDENING_PLAN.md` before adding PgBouncer — in transaction mode it
  breaks the session-variable assumption that RLS depends on here.
- **Migrations, always as the owner.** `manage.py migrate` refuses under the
  app role on purpose. Use `render/release-api.sh`, or set `DATABASE_URL` to
  the owner URL for that one command.

## If something goes wrong

| Symptom | Cause |
|---|---|
| Build fails on `ALLOWED_HOSTS must be set` | Step 2's variables were not set before deploying |
| `FIELD_ENCRYPTION_KEY is not a valid Fernet key` | A random string was used; generate a real one (step 1) |
| Signed out after ~15 minutes | The SPA is not same-origin with the API — check the rewrite (step 3) |
| `Refusing to migrate as 'generic_hrms_app'` | Working as designed; migrate with the owner URL (step 5) |
| Everything returns 403, or lists are empty for everyone | The runtime role is confined but something binds no organization. Check `connected as` in step 7 and the application logs for `dbguard` warnings |
| Uploaded documents vanished | No `S3_BUCKET`; the filesystem is ephemeral (step 1) |
