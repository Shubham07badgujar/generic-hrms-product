"""Development settings."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

INSTALLED_APPS += ["django_extensions"]  # noqa: F405

# The SPA runs on Vite's dev server.
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# No HTTPS locally, so the refresh cookie cannot be Secure or the browser
# silently drops it.
REFRESH_COOKIE_SECURE = False

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Run Celery tasks inline so a dev machine needs no worker process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# No Redis dependency on a dev machine: throttling and caching use local
# memory. Production keeps the Redis cache from base.py.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# NO login-rate override here any more.
#
# There used to be one, raising `login` to 200/hour so a demo could sign in as a
# dozen roles in a row without hitting the old production limit of 10/hour. Base
# is now 150/hour, which covers that on its own, so the override bought nothing
# but a gap between what developers experience and what users get — and a rate
# limit is precisely the thing that should not behave differently in the
# environment where it is being worked on.
#
# It was also a trap. `REST_FRAMEWORK[...] = ...` mutates the dict base.py
# built, in place, so `config.settings.base` reported dev's number to anything
# that read it — including a test written to assert the shipped configuration.
# Left in, it would have cheerfully confirmed a rate the deployment never used.

LOGGING["root"]["level"] = "DEBUG"  # noqa: F405
