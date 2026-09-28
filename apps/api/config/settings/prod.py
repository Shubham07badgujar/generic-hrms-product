"""
Production settings.

Refuses to boot on a misconfiguration rather than starting insecurely — a
silent fallback to a weak setting is worse than a failed deploy.
"""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F403

DEBUG = False

if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("ALLOWED_HOSTS must be set in production.")
if not CORS_ALLOWED_ORIGINS:  # noqa: F405
    raise RuntimeError("CORS_ALLOWED_ORIGINS must be set in production.")

# --- HTTPS / cookies ------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
REFRESH_COOKIE_SECURE = True

# --- Headers --------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Client identity behind the proxies -----------------------------------
# Requests arrive through TWO hops: the shared Caddy terminates TLS and sets
# X-Forwarded-For to the real client, then nginx appends Caddy's own address.
#
# Without this, DRF falls back to REMOTE_ADDR — which is nginx, identically for
# every visitor. Rate limits are keyed on that identity, so the login limit of
# 10/hour stopped being per-client and became a single global budget: any ten
# sign-ins locked out the entire organisation for an hour. It also gave an
# attacker the same allowance as everyone else combined, which is the opposite
# of what a login throttle is for.
#
# 2 = the number of proxies whose entries should be discarded from the right of
# X-Forwarded-For, leaving the client that Caddy recorded.
#
# Configurable because the count is a fact about the HOST, not about this
# application, and it is wrong to hard-code it for one. A platform that puts a
# single router in front of the service wants 1; get it wrong in either
# direction and DRF reads the wrong address, at which point the login throttle
# either counts every visitor as one person or trusts a header the client can
# set. Default 2, so the existing deployment is unchanged.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "NUM_PROXIES": env.int("NUM_PROXIES", default=2),  # noqa: F405
}

# --- Storage --------------------------------------------------------------
# Uploads are resumes, employee documents, letters and payslip PDFs — all
# personal data, none of it ever served directly.
#
# Object storage is the preferred option: private ACL, signed URLs that expire
# in 15 minutes, and nothing user-supplied on the application disk. Set
# S3_BUCKET and it is used.
#
# Without a bucket the fallback is a mounted volume. This is a DELIBERATE
# fallback, not a default — S3 was previously mandatory, which meant a host
# with no object storage could not boot at all. The volume must be a real
# mount that survives the container, and it is the operator's job to back it
# up; a container-local path would silently lose every uploaded document on the
# next deploy.
if env("S3_BUCKET", default=""):  # noqa: F405
    STORAGES["default"] = {  # noqa: F405
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET"),  # noqa: F405
            "region_name": env("S3_REGION", default="ap-south-1"),  # noqa: F405
            "endpoint_url": env("S3_ENDPOINT", default=None),  # noqa: F405
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": 900,
        },
    }
else:
    import logging

    MEDIA_ROOT = env("MEDIA_ROOT", default="/var/lib/hrms/media")  # noqa: F405
    MEDIA_URL = None  # never served by the application

    logging.getLogger("hrms.access").warning(
        "No S3_BUCKET configured — uploads are written to %s. That path MUST be "
        "a mounted volume with its own backup, or every uploaded document is "
        "lost on the next deploy.",
        MEDIA_ROOT,
    )

CELERY_TASK_ALWAYS_EAGER = False

# --- Bootstrap safety -----------------------------------------------------
# Leaving the bootstrap token set in production after the Admin account exists
# leaves a privileged endpoint exposed. Fail loudly instead.
if ADMIN_BOOTSTRAP_TOKEN:  # noqa: F405
    import logging

    logging.getLogger("hrms.access").warning(
        "ADMIN_BOOTSTRAP_TOKEN is set. Remove it from the environment once the "
        "Admin account has been created — the bootstrap endpoint is live."
    )

# --- Monitoring -----------------------------------------------------------
if env("SENTRY_DSN", default=""):  # noqa: F405
    sentry_sdk.init(
        dsn=env("SENTRY_DSN"),  # noqa: F405
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship PII to a third party
        # send_default_pii=False is NOT sufficient on its own. It governs
        # request bodies, cookies and user identity; it does nothing about
        # stack-frame local variables, which Sentry captures by default.
        #
        # Candidate import loops over parsed spreadsheet rows, so any unhandled
        # exception inside that loop has a frame holding somebody's name, phone
        # and address — which would then be shipped to a third party as part of
        # the traceback. The importer already raises PII-free errors; this
        # closes the path it cannot control.
        include_local_variables=False,
    )
