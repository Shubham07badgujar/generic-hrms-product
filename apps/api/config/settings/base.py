"""
Base Django settings — shared by dev and prod.

ARCHITECTURAL NOTE — SINGLE ORGANIZATION
----------------------------------------
This system serves exactly one organization. There is deliberately NO tenant
scoping: no `organization` FK, no TenantManager, no CurrentOrgMiddleware, no
`use_org()` context manager.

The previous system was multi-tenant and suffered a cross-tenant data breach
(HRMS-INC-20260717-01) because tenant context was established in Django
middleware — before DRF resolved a token-authenticated user — so the tenant
context var stayed unset and the tenant manager failed *open*, returning rows
across organizations. Since this system is JWT-authenticated on every request,
that failure mode would have applied universally. Removing tenancy removes the
entire class of bug.

Access control is therefore purely role + scope based; see `core/access/`.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "django_celery_beat",
]

LOCAL_APPS = [
    "core",
    "apps.accounts",
    "apps.statutory",
    "apps.organization",
    "apps.employees",
    "apps.assets",
    "apps.itaccounts",
    "apps.workflows",
    "apps.recruitment",
    "apps.onboarding",
    "apps.offboarding",
    "apps.attendance",
    "apps.leave",
    "apps.payroll",
    "apps.policies",
    "apps.notifications",
    "apps.reporting",
    # Staging for externally sourced candidate data. Before `audit`, like every
    # other app, so its models are registered when audit connects its signals.
    "apps.imports",
    # `audit` is LAST on purpose: its AppConfig.ready() connects signals for
    # every model registered by the apps above, so all registrations must
    # already have run.
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Binds request.user + request id for audit attribution.
    "core.middleware.RequestContextMiddleware",
    # Blanket unsafe-method block for read-only principals (CEO). This is one of
    # four independent mechanisms enforcing read-only; it exists to cover routes
    # that a future developer forgets to map. See core/access/README.md.
    "core.access.middleware.ReadOnlyPrincipalMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# --------------------------------------------------------------------------
# Password hashing / validation
# --------------------------------------------------------------------------

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# DRF
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    # ContextBindingJWTAuthentication, not the stock class: JWT is resolved
    # inside DRF dispatch, after middleware, so the stock class would leave the
    # audit-attribution ContextVar holding AnonymousUser for every API write.
    # See core/auth.py.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.auth.ContextBindingJWTAuthentication",
    ],
    # RBACPermission fails closed: any view without `access_resource` and
    # without `access_exempt = True` is denied. A Django system check
    # (core.access.checks) fails `manage.py check` for unmapped views, so this
    # is a build-time guarantee rather than a runtime hope.
    # NOTE: `core.access.permissions`, NOT `core.access.drf`. DRF resolves this
    # setting from inside rest_framework.views at import time, so the module
    # named here must not import viewsets/generics — see the docstring in
    # core/access/permissions.py.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        # A temporary password unlocks exactly one thing: replacing itself.
        # Checked before RBAC so it applies to every route, mapped or not.
        "core.access.permissions.PasswordChangeRequired",
        # A new joiner with mandatory onboarding outstanding reaches only
        # their onboarding, their documents and their own profile.
        "core.access.permissions.OnboardingGate",
        "core.access.permissions.RBACPermission",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.api.pagination.PageNumberPagination",
    "PAGE_SIZE": 40,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.api.exceptions.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Sign-in is limited on two axes at once — see core/api/throttling.py.
        # `login` is per calling machine, `login_account` is per account being
        # signed in to, and a request must satisfy both.
        #
        # 150/hour per machine supports a working pattern the old 10/hour made
        # impossible: a shared office address behind one NAT, and test passes
        # that exercise every role in one sitting. The per-account ceiling is
        # what stops that being a straight loosening — one account cannot be
        # tried more than 150 times an hour however many machines join in,
        # which is a limit that did not previously exist at all.
        "login": "150/hour",
        "login_account": "150/hour",
        # Bulk candidate import, covering BOTH preview and commit. The
        # parse is the expensive, attackable half, so it is counted the
        # same as the write — and ONLY those two spend the budget (see the
        # viewset's get_throttles); the platform list, batch polling and
        # row edits are ordinary UI traffic and go uncounted. Per user, and
        # backed by a durable 24-hour row budget in the database — Redis
        # counters reset on restart and cannot express "rows" anyway.
        # Forty an hour lets HR work a stack of exports in one sitting.
        "candidate_import": "40/hour",
        # The public application form: the only anonymous write in the API.
        # Per calling address. Twenty an hour is more than any person fills in
        # and far fewer than a script wants.
        "public_apply": "20/hour",
        # Password changes: generous for a person, tight for a brute-forcer
        # working the current-password check.
        "password_change": "10/hour",
        "bootstrap": "5/hour",
        "credential_handoff": "10/hour",
        "user": "2000/hour",
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "HRMS API",
    "DESCRIPTION": "Single-organization HRMS — recruitment, employees, payroll, assets, BI.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
# Access tokens are short-lived and held in memory by the SPA (never in
# localStorage). Refresh tokens live in an httpOnly/Secure/SameSite=Strict
# cookie, rotate on every use, and are blacklisted on reuse so a stolen
# refresh token is detectable and self-revoking.

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    # `or SECRET_KEY`, not just a default: django-environ returns "" for a
    # variable that is PRESENT but blank, so a stray `JWT_SIGNING_KEY=` line in
    # .env would otherwise yield an empty HMAC key.
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default="") or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.api.serializers.TokenObtainSerializer",
}

REFRESH_COOKIE_NAME = "hrms_refresh"
REFRESH_COOKIE_SECURE = env.bool("REFRESH_COOKIE_SECURE", default=True)
REFRESH_COOKIE_SAMESITE = "Strict"

# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True  # required for the refresh cookie

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=env("REDIS_URL", default="redis://localhost:6379/0"))
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TIMEZONE = env("TIME_ZONE", default="Asia/Kolkata")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/1"),
    }
}

# --------------------------------------------------------------------------
# Encryption (PII at rest)
# --------------------------------------------------------------------------
# Fernet key for PAN / Aadhaar / bank account encryption.
# LOSING THIS KEY MAKES EVERY ENCRYPTED FIELD PERMANENTLY UNREADABLE.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

# --------------------------------------------------------------------------
# Admin bootstrap
# --------------------------------------------------------------------------
# The bootstrap route is registered ONLY when a token is configured. An unset
# token makes the URL a genuine 404 rather than a 403, so the endpoint cannot
# be probed once bootstrapping is complete. Remove the token from the
# environment after creating the Admin account.
ADMIN_BOOTSTRAP_TOKEN = env("ADMIN_BOOTSTRAP_TOKEN", default="")
ADMIN_BOOTSTRAP_ALLOWED_IPS = env.list("ADMIN_BOOTSTRAP_ALLOWED_IPS", default=["127.0.0.1"])

# --------------------------------------------------------------------------
# Business rules
# --------------------------------------------------------------------------

# Candidate personal data becomes purge-eligible this long after the final
# hiring decision. Purge is anonymisation, never row deletion, and always
# requires explicit HR/Admin approval. See apps/recruitment/services/retention.py
CANDIDATE_RETENTION_MONTHS = env.int("CANDIDATE_RETENTION_MONTHS", default=12)

# A much shorter clock for candidates acquired in bulk who never affirmed
# consent. We approached them; they never asked to be in our system, and data
# minimisation is the strongest argument available for holding
# third-party-sourced data at all — an argument worth little if the holding is
# indefinite. Once they reply and consent, the clock above takes over instead.
CANDIDATE_UNAFFIRMED_RETENTION_DAYS = env.int(
    "CANDIDATE_UNAFFIRMED_RETENTION_DAYS", default=90
)

# Minimum characters for a rejection rationale. Enforced at DB (CheckConstraint),
# serializer, and UI — three layers so no client can bypass it.
REJECTION_REASON_MIN_LENGTH = 20

# --------------------------------------------------------------------------
# Storage / static
# --------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------
#
# READ THIS BEFORE ASSUMING A FILE HAS A SIZE CEILING.
#
# Django has no FILE_UPLOAD_MAX_SIZE. `DATA_UPLOAD_MAX_MEMORY_SIZE` is
# documented as EXCLUDING file fields, so none of the settings below cap how
# large an uploaded file may be — they cap the non-file body, the number of
# parts, and how much is held in RAM before spilling to disk.
#
# The ceiling on a single file therefore has to come from two places that are
# not here: the reverse proxy (nginx `client_max_body_size 25m`) and an
# explicit check in the service that accepts it (`core.validators`). Both are
# required; neither alone is sufficient.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440       # 2.5 MiB of non-file POST body
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440       # spill to a temp file above this
DATA_UPLOAD_MAX_NUMBER_FILES = 5
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
# Spooled uploads are candidate PII in transit; keep them off other users.
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700

# --------------------------------------------------------------------------
# I18N
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-in"
TIME_ZONE = env("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="HRMS <noreply@localhost>")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

#: Seconds any single SMTP conversation may take. Django's default is None —
#: no timeout at all — which turns a mail server that accepts the connection
#: and then stops answering into a permanently blocked thread. Every sender in
#: this codebase already catches a failed send, records it on the row and moves
#: on; a hang is not a failure, so that `except` never runs, the request never
#: returns, and enough of them exhaust the worker pool. Bounding the socket is
#: what makes the recovery code reachable.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=15)

#: A SECOND authenticated sender for HR's own mail — the employee welcome /
#: account email — so candidate-facing recruitment mail and HR's employee
#: mail can come from different mailboxes. All three unset (the default)
#: means everything sends through the primary account above, exactly as
#: before. Same host/port/TLS as the primary; only the identity differs.
HR_EMAIL_HOST_USER = env("HR_EMAIL_HOST_USER", default="")
HR_EMAIL_HOST_PASSWORD = env("HR_EMAIL_HOST_PASSWORD", default="")
HR_FROM_EMAIL = env("HR_FROM_EMAIL", default="")

#: Where the SPA lives, for links in outbound mail. Derived from the first
#: CORS origin unless set explicitly, so a deployment that already knows its
#: frontend origin does not have to say it twice.
FRONTEND_URL = env(
    "FRONTEND_URL",
    default=(CORS_ALLOWED_ORIGINS[0] if CORS_ALLOWED_ORIGINS else "http://localhost:5173"),
)
#: How the organisation is named in mail subjects and bodies. Only a fallback:
#: outbound mail prefers the name in OrgSettings, which HR maintains.
ORG_DISPLAY_NAME = env("ORG_DISPLAY_NAME", default="HRMS")
#: Where a new employee should write if they cannot get in. Defaults to the
#: sending address, which is always monitored by whoever set the mail up.
HR_CONTACT_EMAIL = env("HR_CONTACT_EMAIL", default=DEFAULT_FROM_EMAIL)

# Google Forms as an external application form. OFF unless a service-account
# key file is configured; the HRMS-hosted /apply/<token> page exists either
# way. See apps/recruitment/services/external_forms.py.
GOOGLE_FORMS_CREDENTIALS_FILE = env("GOOGLE_FORMS_CREDENTIALS_FILE", default="")
GOOGLE_FORMS_SHARE_WITH = env("GOOGLE_FORMS_SHARE_WITH", default="")
# A Drive folder a REAL account owns and has shared with the service account
# as Editor. Service accounts have no Drive storage, so forms are created into
# this folder (and belong to its owner) rather than into the account's own
# Drive, where creation fails. Required in practice for Google Forms to work.
GOOGLE_FORMS_FOLDER_ID = env("GOOGLE_FORMS_FOLDER_ID", default="")
# A real Google user's one-time OAuth grant — the route that works on a
# Gmail-based organisation, where a service account cannot own a form.
# CLIENT_FILE is the OAuth client JSON from Google Cloud (Desktop app);
# TOKEN_FILE is written by `manage.py google_forms_authorize` and is a secret.
GOOGLE_FORMS_OAUTH_CLIENT_FILE = env("GOOGLE_FORMS_OAUTH_CLIENT_FILE", default="")
GOOGLE_FORMS_OAUTH_TOKEN_FILE = env("GOOGLE_FORMS_OAUTH_TOKEN_FILE", default="")

#: Google Calendar for interviews — shares the OAuth grant above. The event
#: is created in this calendar of the granting account; "primary" is that
#: person's own calendar. Meet rooms are attached to every event unless
#: disabled.
GOOGLE_CALENDAR_ENABLED = env.bool("GOOGLE_CALENDAR_ENABLED", default=True)
GOOGLE_CALENDAR_ID = env("GOOGLE_CALENDAR_ID", default="primary")
GOOGLE_MEET_ENABLED = env.bool("GOOGLE_MEET_ENABLED", default=True)

# eSSL eTimeTrackLite biometric attendance. OFF unless explicitly enabled AND
# a base URL is set; every sync path no-ops meanwhile. Credentials stay in
# the environment and are read only inside apps/attendance/services —
# nothing here is ever serialized to the frontend.
ESSL_INTEGRATION_ENABLED = env.bool("ESSL_INTEGRATION_ENABLED", default=False)
ESSL_BASE_URL = env("ESSL_BASE_URL", default="")
ESSL_USERNAME = env("ESSL_USERNAME", default="")
ESSL_PASSWORD = env("ESSL_PASSWORD", default="")
#: Devices report naive local wall-clock time; this names whose clock.
ESSL_TIMEZONE = env("ESSL_TIMEZONE", default="Asia/Kolkata")
#: Minimum minutes between scheduled pulls (beat ticks every 5; this governs).
ESSL_SYNC_INTERVAL = env.int("ESSL_SYNC_INTERVAL", default=5)
#: The report-only switch: while False, attendance is computed and shown but
#: payroll keeps its assumed-present arithmetic untouched. Flipping this is a
#: management decision, not a deploy side effect.
ATTENDANCE_AFFECTS_PAYROLL = env.bool("ATTENDANCE_AFFECTS_PAYROLL", default=False)

#: How long a freshly generated payslip stays deletable by the Finance Head
#: (with a recorded reason). Past this window the payslip is locked for
#: everyone. A policy number, so it lives here rather than in code.
PAYSLIP_DELETE_WINDOW_DAYS = env.int("PAYSLIP_DELETE_WINDOW_DAYS", default=5)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Denials are security-relevant; never silence this logger.
        "hrms.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "hrms.audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
